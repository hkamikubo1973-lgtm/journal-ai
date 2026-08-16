# journal-ai 正式UI化 Phase 3 登録API設計前調査

調査基準コミット: `5ad2bfd`
対象: 通常仕訳モード。未収消込の登録経路は比較対象に含めるが、本設計の対象外とする。

## 1. 調査目的

React版の登録機能を追加する前に、Streamlit版における「登録」の意味、エプソン45列CSV・入力用Excel・検索DB (`data/transactions.csv`) の更新タイミング、二重登録防止を整理する。

今回の結論は次のとおり。

- Streamlitの「この内容で登録」は永続化ではなく、出力待ちカートである `st.session_state.confirmed` への追加である。
- 入力用Excelの保存・ダウンロードと、エプソンCSVのダウンロードでは検索DBを更新しない。
- エプソンCSVを設定済み保存先へ保存できた場合だけ、重複確認後に検索DBを更新する。
- 初期のReact版は通常1行仕訳だけを対象にし、サーバー側で登録予定行を組み立てる `prepare-registration` を先行させるべきである。
- 出力待ちカートは当面React stateで保持し、FastAPIプロセスのメモリには保持しない方が安全である。

## 2. 現行Streamlit通常仕訳登録フロー

現行フローは以下である。

1. 検索結果の候補を選択する。
2. 候補の元行を `split_journal()` に渡す。
3. 分割後の各行について、借方・貸方、補助、金額、摘要を編集する。
4. 元行を `deepcopy` し、編集値を上書きして `edited_rows` を作る。
5. 金額未入力・0以下を拒否し、伝票全体の借貸合計を確認する。
6. 「この内容で登録」で `deepcopy(edited_rows)` を `st.session_state.confirmed` に追加する。
7. `confirmed` の各伝票は、出力前に再編集・更新保存・削除できる。
8. `confirmed` を平坦化して入力用ExcelとエプソンCSVを生成する。
9. エプソンCSVを保存先へ保存できた時だけ、二重登録確認後に検索DBへ追記する。

主な実装箇所:

- 候補の分割・編集: `src/app.py:3935`, `src/app.py:3988`, `src/app.py:4130` 付近
- 編集行への最終反映: `src/app.py:4390` 付近
- `confirmed` への追加: `src/app.py:4436`-`4456`
- 登録済み仕訳の再編集: `src/app.py:4476`-`4635`
- 出力用の平坦化: `src/app.py:4646`-`4650`

### 候補行と複数行仕訳

`split_journal()` は次の変換を行う。

- 1対多: 1本の借方を基準に貸方行ごとの行へ分割し、各行の借貸金額を貸方行金額へそろえる。
- 多対1: 貸方を共通化し、借方行ごとの行へ分割し、各行の借貸金額を借方行金額へそろえる。
- その他: 元行をそのまま返す。

同じ規則は検索API用の `src/journal_search_service.py:160` にも複製され、`editable_rows` は `build_editable_rows()` で生成される。API化では、この規則を単一のサービス実装へ集約し、StreamlitとFastAPIでパリティテストを行う必要がある。

### 資金複合・諸口

「資金複合」「諸口」は編集用科目候補から除外される。該当候補では同一伝票ブロックを参照表示し、利用者が実際の相手科目へ変更する。React側でも推論・自動置換は行わず、初期登録APIでは該当候補を警告または登録対象外にするのが安全である。

## 3. confirmed の構造と役割

通常仕訳の `confirmed` は概ね次の型である。

```text
list[                 # 出力待ち伝票の一覧
  list[               # 1伝票。分割後は複数行になり得る
    dict[str, value]  # 元のエプソン45列を含む編集済み行
  ]
]
```

通常仕訳の追加処理は `confirmed.append(deepcopy(edited_rows))` である。したがって「登録済仕訳」という画面表現でも、実体はセッション内の出力待ちデータであり、まだ検索DBには永続化されていない。

`iter_confirmed_journal_rows()` は互換性のためdict単体とlistの両方を受け付けるが、通常仕訳の出力処理は各要素を伝票行listとして `all_rows.extend(doc)` している。APIスキーマでは曖昧なunionを持ち込まず、`documents: [{ rows: [...] }]` のように伝票境界を明示すべきである。

`confirmed` はStreamlitセッションに依存するため、ブラウザセッション終了、アプリ再起動、別セッションでは失われる。この一時性は出力待ちデータとしては誤保存を防ぐ側面もある。

## 4. 編集フォーム値の反映処理

候補選択後、分割済み各行の元dictを深いコピーし、次を上書きする。

| 項目 | 現行処理 |
|---|---|
| 伝票日付 | 画面共通の処理日 `process_date` を各行へ設定 |
| 借方・貸方 | 科目名を選択し、`借方科目名` / `貸方科目名` へ設定 |
| 借方・貸方補助 | 補助名を各列へ設定 |
| 金額 | 1つの入力金額を借方金額・貸方金額へ同額設定 |
| 摘要 | 画面入力値を設定 |
| 部門 | 通常仕訳編集画面では変更せず、元の45列雛形を保持 |
| 伝票摘要 | 摘要からコピーせず、元のDB雛形値を保持 |
| 税・資金区分・インボイス等 | 元の45列雛形を保持 |

金額は各行で正整数が必要で、未入力・0以下なら登録できない。各行の同じ金額を借貸へ設定するため通常は借貸合計が一致するが、伝票全体でも `d_sum == c_sum` を確認している。未来日付は警告のみで、登録阻止条件ではない。

`confirmed` の再編集でも、1つの金額入力を借方金額・貸方金額へ同額設定する。

科目コードは編集時には直接確定せず、エプソン行生成時に科目名からマスターを引く。補助コードも同様である。部門コード・部門名をReactで編集可能にしている現状と、Streamlit出力が部門を元雛形のまま保持する挙動は一致していないため、登録API実装前に仕様確定が必要である。

## 5. エプソンCSV・入力用Excel出力との関係

### 入力用Excel

`build_input_csv_rows(confirmed)` が伝票境界を平坦化し、簡易12列へ変換する。`build_input_journal_excel()` はそのDataFrameを印刷・手入力向けExcelへ整形する。

- 45列インポート形式ではない。
- 保存先への保存でも検索DBを更新しない。
- ダウンロードでも検索DBを更新しない。

### エプソン45列CSV

`build_epson_rows(all_rows, company_name)` は各編集済み行から `EPSON_COLUMNS` 45列のdictを作る。

1. 元行から45列をコピーする。
2. 画面編集対象の日付・摘要・借貸科目名・補助名・借貸金額を上書きする。
3. 科目コードを科目マスターまたは検索データの名前→コード対応から解決する。
4. 補助コードを補助マスターから解決し、解決できなければ元コードへフォールバックする。
5. 入力マシン・入力ユーザ・入力アプリ・入力会社・入力日付を出力時の値へ更新する。
6. `EPSON_COLUMNS` 順でDataFrame化し、CP932のCSVを生成する。

伝票摘要はDB雛形の値を保持し、摘要からの自動コピーを明示的に禁止している。税区分、資金区分、任意項目、インボイス情報なども元雛形を保持する。

注意点として、エプソンCSVは `epson_rows` から生成する一方、検索DB登録には出力メタデータを上書きする前の `all_rows` を渡している。入力会社を含むバッチIDも `all_rows` から作る。この差が意図した互換性か、検索DBにも新しい入力会社等を保存すべきかは要確認である。

## 6. transactions.csv 登録タイミング

現行の永続化条件は次のとおり。

| 操作 | ファイル生成・保存 | transactions.csv更新 |
|---|---:|---:|
| 「この内容で登録」 | なし | しない |
| 入力用Excel ダウンロード | Excel返却 | しない |
| 入力用Excel 保存先へ保存 | Excel保存 | しない |
| エプソンCSV ダウンロード | CSV返却 | しない |
| エプソンCSV 保存先へ保存 | CSV保存 | 重複確認後に更新 |

エプソンCSV保存は先に実行され、成功後に検索DB登録を行う。DB登録に失敗した場合でもCSVファイルはすでに保存済みで、画面には「CSVは保存したが検索DB登録に失敗」と表示される。現行処理はファイル保存とDB更新の原子性を保証しない。

`register_epson_rows_to_search_db(all_rows)` は更新前後の行数を比較し、`update_search_csv([all_rows])` を呼ぶ。`update_search_csv()` は次を行う。

- 全値を文字列化・trimし、連続空白を統一する。
- 借貸金額が両方0の行、日付がない行を除外する。
- 新規行を既存行の先頭へ追加する。
- `year >= current_year - 3` の行だけを残す。
- 固定45列・UTF-8 BOM付きで `data/transactions.csv` 全体を書き直す。

コメントは「3年保持」だが、境界は `current_year - 3` を含むため、暦年ベースでは現在年を含め最大4年分になり得る。API化時も現行互換を維持するか、別途仕様化する必要がある。

## 7. 二重登録防止ロジック

通常仕訳の二重登録防止は二段構えである。

### 1. Streamlitセッション内バッチID

`build_normal_journal_batch_id(all_rows)` が、正規化した行キーを並べ替え、JSON化してSHA-256を生成する。対象列は以下である。

- 日付
- 借貸の科目コード・科目名・補助コード・補助名・金額
- 摘要・伝票摘要
- 入力会社

正規化はNFKC、空白統一、日付区切り除去、金額カンマ除去を行う。行キーをsortするため行順には依存しない。

保存済みIDは `st.session_state.registered_normal_journal_batch_ids` のsetに保持される。これは同一セッション内の二度押しには有効だが、再起動・別ブラウザ・複数worker間では共有されない。

### 2. transactions.csv既存行照合

セッションsetに無い場合、`is_normal_journal_batch_in_transactions(all_rows)` が同じ正規化行キーのCounterを既存CSV全行と比較する。対象バッチと同じ各行が必要個数以上存在すれば登録済みと判断する。

既存と判定した場合でもエプソンCSVは保存するが、DB追記をスキップする。照合中の例外は未登録扱いになるため、読込障害時に重複追記へ進む可能性がある。

### API化で不足する点

- `update_search_csv()` 自体には重複排除がない。
- CSV全体のread-modify-writeにファイルロックがない。
- 「照合→追記」は原子的でなく、同時リクエストが両方通過できる。
- session setはプロセス再起動、複数worker、別クライアントをまたげない。
- CSV保存成功後・DB失敗の部分成功を再試行した場合の扱いを明示する必要がある。
- 3年保持で過去行が落ちた後は、transactions.csv照合だけでは過去バッチの保存履歴を証明できない。

## 8. React/FastAPI化で注意すべき点

### confirmedをどこに持つか

| 方式 | 長所 | 短所 | 評価 |
|---|---|---|---|
| React stateのみ | サーバーがステートレス。再起動・複数worker問題がない。一時データが意図せず残らない | 画面更新で消える。クライアント値をサーバーで再検証する必要 | 初期版に推奨 |
| FastAPIメモリ | APIでカートを共有しやすい | reload・再起動で消失。workerごとに分裂。利用者識別が必要 | 非推奨 |
| 一時ファイル | 再起動後も復元可能 | ロック、掃除、利用者分離、個人情報管理が必要 | 初期版では過剰 |
| Reactで保持し出力時に一括送信 | 現行confirmedの一時性を維持。サーバーをステートレスにできる | バッチ全体を毎回検証する必要 | 最も推奨 |

1社・ローカル運用の初期版では、`prepare-registration` の結果をReact stateの出力待ちカートへ追加し、出力時に全件を送る方式が安全である。ページ更新で失われることはUIで明示する。必要になった時点でsessionStorage等を別Phaseで検討する。

FastAPIはクライアントから返送された45列やprepared結果を信頼せず、出力・保存時に再検証・再整形する。

### 候補雛形の識別

45列の税・資金区分・インボイス等を保持するには元雛形が必要である。理想は検索レスポンスに安定した `candidate_id` / `source_fingerprint` を追加し、prepare時にサーバーが元行を再解決する方式である。

移行期に `template_row` をクライアントから送る場合は、45列へ限定し、編集可能列を明示的に上書きし、hidden列の改変を検査する。ただしこれは完全な改ざん防止にはならない。

### 初期対応範囲

Phase 3初期は次を満たす通常1行仕訳に限定する。

- `editable_rows.length == 1`
- 正の共通金額がある
- 借貸科目が空でなく、資金複合・諸口ではない
- 日付形式が妥当
- 科目・補助・部門のコードと名称がサーバーマスターで確認できる

複数行、資金複合、諸口は警告だけで登録可能にせず、`unsupported_multi_row` / `complex_account_requires_review` としてprepareをblockedにする方が安全である。

## 9. 登録APIのリクエスト案

### POST `/api/journal/prepare-registration`

副作用を持たず、React編集値から登録・出力予定行を組み立てる。

```json
{
  "client_request_id": "uuid",
  "mode": "single",
  "candidate_ref": {
    "candidate_id": "server-issued-id",
    "pattern_key": ["220", "114", "1", "2"],
    "source_fingerprint": "sha256"
  },
  "form": {
    "voucher_date": "20240719",
    "voucher_no": "",
    "voucher_summary": "",
    "debit": {
      "account_code": "220",
      "account_name": "長期借入金",
      "sub_code": "1",
      "sub_name": "埼玉りそな",
      "department_code": "",
      "department_name": ""
    },
    "credit": {
      "account_code": "114",
      "account_name": "普通預金",
      "sub_code": "2",
      "sub_name": "埼玉りそなＳＴ",
      "department_code": "",
      "department_name": ""
    },
    "amount": "20000000",
    "summary": "㈱埼玉りそな銀行　保証協会付"
  }
}
```

サーバー側の責務:

- candidate/sourceを再解決する。
- 日付・正金額・必須科目・コード名称対応を検証する。
- 資金複合、諸口、複数行を初期版では拒否する。
- 元45列雛形をコピーし、許可された編集項目だけを上書きする。
- 共通金額を借方金額・貸方金額へ同額設定する。
- totals、warnings、stable item IDを返す。
- transactions.csvや出力先には書かない。

### POST `/api/journal/register`

初期版では必須ではない。React state方式の場合、「出力対象へ追加」はprepare成功レスポンスをクライアントのカートへ追加する操作で完結する。

互換上このendpointを設ける場合も、意味は「prepared itemを返す」であり、検索DB永続化ではないことを名前・レスポンスで明示する。FastAPIメモリへconfirmedを保持する実装は避ける。

### 出力API候補

- `POST /api/journal/export/input-excel`: Excelを返す。DB更新なし。
- `POST /api/journal/export/epson/download`: CP932 CSVを返す。DB更新なし。
- `POST /api/journal/export/epson/save`: 設定済み保存先へ保存し、冪等性確認後にDB更新する。

保存先はクライアントから任意パスを受けず、サーバー設定済みルートと許可済みサブディレクトリから決定する。

## 10. 登録APIのレスポンス案

### prepare成功

```json
{
  "status": "ready",
  "persisted": false,
  "prepared_item_id": "sha256",
  "document": {
    "rows": [{ "伝票日付": "20240719", "借方金額": "20000000", "貸方金額": "20000000" }]
  },
  "totals": {
    "debit": 20000000,
    "credit": 20000000,
    "balanced": true
  },
  "warnings": [],
  "errors": []
}
```

### prepare警告・拒否

```json
{
  "status": "blocked",
  "persisted": false,
  "prepared_item_id": null,
  "document": null,
  "warnings": [
    { "code": "complex_account_requires_review", "message": "資金複合または諸口を実科目へ修正してください" }
  ],
  "errors": [
    { "code": "unsupported_multi_row", "field": "candidate", "message": "初期版は通常1行仕訳のみ対応します" }
  ]
}
```

### エプソンCSV保存

```json
{
  "status": "saved_and_registered",
  "batch_id": "sha256",
  "idempotency_key": "uuid-or-batch-id",
  "csv_saved": true,
  "saved_path": "configured-relative-path",
  "db_registered": true,
  "already_registered": false,
  "appended_count": 1,
  "warnings": []
}
```

部分成功は `csv_saved_db_failed`、既存登録済みは `saved_already_registered` として、HTTP成功/失敗だけでなく状態を明示する。

## 11. サービス層へ切り出すべき処理

候補として `src/journal_registration_service.py` を追加し、UI・HTTP・ファイルI/Oから次を分離する。

1. 候補元行の解決とsource fingerprint検証
2. `split_journal` の共通実装
3. 単一行フォームの検証と45列行への反映
4. 科目・補助・部門のコード名称解決
5. 共通金額の借貸同額反映と借貸合計検証
6. 入力用Excel向け簡易行の生成
7. エプソン45列行の生成
8. 正規化行キー、prepared item ID、batch ID生成
9. transactions.csv既存行との重複判定
10. 更新対象行のclean・valid判定・保持期間適用

I/O層は別にし、以下を明確に分ける。

- pure: validate / prepare / build rows / IDs
- read: masters / transactions / source candidate
- write: export file / transactions update / persistent idempotency ledger

既存Streamlitは当面このサービスを呼ぶ薄いadapterへ段階的に寄せる。最初から `app.py` を大規模変更しない。

## 12. 実装Phase案

### Phase 3-1: pure service抽出とパリティテスト

- `split_journal`、編集値反映、45列生成、簡易出力行生成、batch IDをサービス化する。
- 既存Streamlitの代表ケースと完全一致するテストを追加する。
- ファイル書込みは行わない。

### Phase 3-2: prepare-registration API（通常1行限定）

- 検索候補にcandidate/source fingerprintを追加する。
- 単一行だけprepareし、prepared rows・警告・エラーを返す。
- Reactの「出力対象へ追加」はprepared結果をstateへ追加する。

### Phase 3-3: 出力待ちカートUI

- React stateに伝票境界付きのprepared documentsを保持する。
- 更新・削除・合計・未保存警告を実装する。
- ページ更新で消えることを明示する。

### Phase 3-4: 副作用なし出力

- 入力用Excel生成・ダウンロードAPIを追加する。
- エプソンCSVダウンロードAPIを追加する。
- どちらもDB登録しないことをテストする。

### Phase 3-5: エプソンCSV保存と冪等DB登録

- 保存先制約、batch ID、idempotency key、ファイルロックを実装する。
- CSV保存、既存照合、DB更新、部分失敗レスポンスを実装する。
- 同時二度押し・再試行・プロセス再起動のテストを追加する。
- 必要なら永続的な保存済みバッチ台帳を導入する。

### Phase 3-6: 複数行仕訳

- 1対多・多対1の行単位金額UIとAPI schemaを設計する。
- 資金複合・諸口の実科目置換確認を含める。
- 単一行共通金額APIと混同しないdiscriminated unionを採用する。

## 13. 未解決・要確認事項

1. Reactで編集可能な借貸部門を、エプソン出力と検索DBへどう反映するか。現行通常仕訳は元雛形を保持する。
2. `伝票番号` と `証番号` のどちらをReactの `voucherNo` が編集するのか。エプソン45列には両方ある。
3. `伝票摘要` をReactで編集可能にするか。現行はDB雛形値を保持し、摘要からのコピーを禁止する。
4. clientから元45列行を返送させる移行案を許容するか、candidate IDによるサーバー再解決を先に実装するか。
5. 科目・補助・部門コードと名称が不一致の場合、名称を正として再解決するか、エラーにするか。
6. 元雛形が無い場合に空45列から登録を許可するか。税・インボイス等が失われるため初期版では拒否が安全。
7. 資金複合・諸口を修正済みなら登録可能とする条件と、修正確認の監査情報。
8. 未来日付を現行どおり警告だけにするか、APIでは確認フラグを要求するか。
9. エプソンCSV保存とtransactions更新の部分成功を、再試行時にどう回復するか。
10. `all_rows` と `epson_rows` のどちらを検索DBへ登録し、入力会社・入力メタデータをどちらに合わせるか。
11. 現行の `year >= current_year - 3` を「3年保持」の正式仕様として維持するか。
12. 永続的なバッチ台帳を作る場合の保存場所、ロック、バックアップ、保持期間。
13. transactions.csv更新時のプロセス間排他と、書込み失敗時の原子的置換方法。
14. `register_epson_rows_to_search_db()` の前後行数差による成功判定を、保持期間による削除が同時発生しても正しく判定できる方式へ変更するか。
