# journal-ai 正式UI化 Phase 3-12 補助親子関係マスター設計

## 1. 目的と決定事項

本書は、科目と補助科目の利用可能関係を既存データから分離して管理するための設計を定める。Phase 3-12では調査と設計だけを行い、CSV、Python、React、APIは変更しない。

今回採用する方針はB案である。

- `data/sub_master.csv` は既存の補助コード・名称マスターとして、そのまま維持する。
- 新しい `data/sub_account_relations.csv` は、科目コードと既存補助の利用可能関係だけを管理する。
- `transactions.csv` は過去に実際に登録された仕訳履歴であり、過去名称を含む事実として変更しない。
- 新マスターの初回ブートストラップに限り、過去仕訳に存在する18関係を初期状態の正として採用する。
- 初期生成後の新マスターは独立した現在マスターとし、過去仕訳から自動再生成・同期・上書きしない。
- 検索エンジン、未収消込、Epson CSV、Excel、Streamlit版の既存処理は、新マスターに依存させない。

この責務分離により、現行の補助コード・補助名称・過去仕訳DBを書き換えず、Reactの科目別補助選択とFastAPIの親子関係検証だけを追加できる。将来、現在名称を変更しても過去仕訳の名称は一括置換しない。

## 2. 調査時点のデータ

`transactions.csv` と `sub_master.csv` は読み取り専用で再集計した。

| 確認項目 | 結果 |
| --- | ---: |
| `transactions.csv` 行数 | 19,944 |
| 借貸合計の補助利用件数 | 6,832 |
| 異なる `(account_code, sub_code, sub_name)` | 18 |
| コード・名称の片側欠落 | 0 |
| 同一 `(account_code, sub_code)` の複数名称 | 0 |
| 履歴にだけ存在する `(sub_code, sub_name)` | 0 |
| `sub_master.csv` にだけ存在する `(sub_code, sub_name)` | 0 |
| 複数科目で再利用される補助コード | `1`, `2`, `3`, `6` |
| 現データの先頭ゼロ付き科目・補助コード | 0 |

現データに先頭ゼロはないが、コードは識別子であり数値ではない。読込、API、Reactの全層で文字列として保持する。

## 3. `sub_master.csv` の全依存箇所

### 3.1 実行対象コードの直接参照

| 箇所 | 読込・利用方法 | 2列・列位置・行順への依存 |
| --- | --- | --- |
| `src/app.py:386` `load_sub_master()` | `csv.DictReader` で `name -> code` の辞書を生成 | `code` と `name` の列名が必須。列位置には非依存。行順そのものには非依存だが、同名行があれば後の行が上書きする |
| `src/app.py:2714` | `SUB_MASTER.keys()`、すなわち補助名称だけをソートし、Streamlitの借貸補助selectへ全件表示 | 元CSVの行順には非依存。科目との組み合わせは見ない |
| `src/app.py:1392` | 未収消込で生成した補助名称を `SUB_MASTER.get(name)` により補助コードへ変換 | 補助名称単独検索。親科目は見ない |
| `src/app.py:1786` | Epson行生成時、借貸の補助名称を `SUB_MASTER.get(name)` によりコードへ変換 | 補助名称単独検索。親科目は見ない |
| `src/app.py:2858` `generate_sub_master()` | 過去仕訳の借貸から `(code, name)` を収集し、`sub_master.csv` 全体を書き直す | 出力を厳密に `code,name` の2列で再生成する。親科目と行順を保持しない |
| `src/journal_master_service.py:128` | `_read_master_rows(..., ("code", "name"))` で読み、`sub_accounts` 配列を作成 | 必須列名は2列。列位置には非依存し、追加列は読めるが利用しない。API配列の並びはCSV行順を保持 |
| `src/journal_registration_service.py:192` | 上記API用マスターをコード別にまとめ、コード存在と名称一致を検証 | 補助コード単独で候補を取得した後、名称を照合。親科目は見ない |
| `src/api/journal.py:149` | `sub_accounts` を `/api/journal/masters` のレスポンス型として公開 | `code`, `name`, `label` のDTOに依存。CSVは間接参照 |

`src/app.py.bak_dummy_to_paddle` と `src/app.py.bak_ocr_api` にも旧Streamlitコードと同じ直接参照がある。これらは現行実行対象ではない退避ファイルだが、将来復元する場合にB案の互換性を保つ理由になる。

### 3.2 補助の検索単位

- 補助名称単独検索: Streamlitの `SUB_MASTER`、未収仕訳のEpson行化、通常Epson行化。
- 補助コード単独検索: `journal_registration_service` のマスター候補取得と、`journal_master_service` の重複コード診断。
- `(sub_code, sub_name)` の照合: `journal_registration_service` が同一コードの名称候補に入力名称があるか検証する。
- 科目と補助の組み合わせ: 現行マスター検証には存在しない。未収雛形検索は過去仕訳行上で借貸科目を先に一致させ、補助名称一致を加点するが、正規の親子関係検証ではない。

### 3.3 間接的に補助列を扱うが `sub_master.csv` を読まない機能

| 機能 | 調査結果 |
| --- | --- |
| 検索エンジン `src/engine.py` | `transactions.csv` の借貸補助名称を検索トークン・150点加点に使い、補助を含む `pattern_key` を作る。`sub_master.csv` は読まない |
| FastAPI検索 `src/journal_search_service.py` | 検索結果の補助列をDTOへ転記するだけで、マスターを読まない |
| 未収消込 `src/receivable_engine.py` | FIFO、残高、差額、生成仕訳で補助名称を扱うが、`sub_master.csv` は読まない。コード化は後段のStreamlit `app.py` が行う |
| AIサーチ | 検索結果・過去仕訳データを利用し、`sub_master.csv` を直接参照しない |
| OCR | `sub_master.csv` を直接参照しない |
| Excel | 入力仕訳帳と未収確認表は渡された表示値を出力し、`sub_master.csv` を直接参照しない |
| Epson CSV | 45列構造自体はマスター非依存。ただし `app.py` のEpson行生成が名称からコードを引く際に `SUB_MASTER` を使う |

## 4. 新親子関係マスターの責務

### `sub_master.csv`

- 既存互換用の補助コード・補助名称マスター。
- 現行の `code,name` と既存18行を維持する。
- Streamlit、Epsonコード変換、現行FastAPIマスターとの互換性を維持する。
- 親科目関係は持たせない。

### `sub_account_relations.csv`

- 正式UIとFastAPIで使用する現在の科目・補助関係を表す。
- `sub_name` は「現在、新規仕訳で使用する補助名称」とする。
- Reactの科目別補助selectとFastAPIの親子関係検証にだけ使用する。
- 検索順位、候補生成、未収消込、出力形式の正本にはしない。
- 初期生成時は `sub_master.csv` と `account_master.csv` へ照合する。初期生成後の現在名称は独立して変更できる。

新ファイルは既存マスターの置換でも、履歴DBの修正でもない。追加の関係情報である。

### 過去DBと現在マスターの分離

```text
transactions.csv
= 過去に実際に登録された仕訳履歴
= 過去名称を含めて事実として保持
= 名称の一括置換、正規化、現在名称への同期を行わない

sub_master.csv
= 既存互換用補助マスター
= 現行Streamlit等の既存処理を保護
= 今回は変更しない

sub_account_relations.csv
= 正式UI/FastAPI用の現在マスター
= 現在の科目コード×補助コード×補助名称を保持
= 初回生成後は過去DBから独立して管理
```

## 5. 列設計 B-1 / B-2

| 評価軸 | B-1 `account_code,sub_code` | B-2 `account_code,sub_code,sub_name` |
| --- | --- | --- |
| 整合性 | 正規化され名称の二重保持がない。ただし現行 `sub_master.csv` では `sub_code` がグローバル一意でないため、名称を一意に結合できない | 科目とコードで現在名称を一意に決定できる。初期生成時は `(sub_code, sub_name)` を既存マスターと照合できる |
| 二重管理 | 低い | 名称を2ファイルに持つため不一致リスクがある |
| 実装の単純さ | 関係存在の検証だけなら単純だが、React表示名の解決には曖昧性解消の別設計が必要 | APIとReactは関係行だけでコード・名称を一組として扱える |
| 将来保守 | `sub_master.csv` に安定した一意IDを追加できれば有力 | 現在名称の変更履歴と、過去DBから独立して管理する運用が必要 |
| 既存コードへの影響 | 既存コードは不変だが、新機能が名称解決できない | 既存コードは不変。新サービスだけで完結できる |

### 推奨: B-2

推奨列は次の3列とする。

```csv
account_code,sub_code,sub_name
```

B-1は一般的な正規化としては望ましいが、現行 `sub_master.csv` では同じ `sub_code` が複数名称に使われる。たとえばコード`1`だけでは「社会保険料」「埼玉りそな東松山」「ＳＴビル」「埼玉りそな」のどれかを決められない。そのためB-1は、既存マスターを変更しないという今回の前提ではReactの表示名を安全に復元できない。

B-2の `sub_name` はキーではなく、正式UIから新しく登録する仕訳で使う現在名称である。初期生成時は履歴名称をそのまま採用し、次を必須検証する。

1. `(sub_code, sub_name)` が `sub_master.csv` に完全一致で存在する。
2. `account_code` が `account_master.csv` に存在する。
3. 同一 `(account_code, sub_code)` が重複しない。
4. 同一 `(account_code, sub_code)` に異なる `sub_name` が存在しない。
5. 必須3項目の空欄、前後空白だけの値、余分な列、重複行を診断する。

初期生成後は、たとえば `114,2,○○銀行` を `114,2,△△○○銀行` へ変更できる。このとき `transactions.csv` の過去名称と `sub_master.csv` は変更しない。したがって通常運用時に、現在名称が既存 `sub_master.csv` にないことだけを理由として新マスターを不正にしてはならない。名称の変更、親子関係の追加、必要に応じた無効化は、将来のマスター管理機能で明示的に行う。今回、その管理UIや無効化列は実装しない。

## 6. キー設計

事実上の複合キーは `(account_code, sub_code)` とする。`sub_name` はそのキーに従属する検証対象値である。

- 同一科目内の同一補助コードは1行だけ許可する。
- 同一補助コードを別科目で再利用できる。
- 同一科目＋同一補助コードに複数名称があればマスターエラーとする。
- `account_code` と `sub_code` は常に文字列で保持する。
- CSV読込で数値型推論を行わず、先頭ゼロを保持する。
- APIのJSONでもコードを文字列として返し、Reactのoption valueも文字列にする。

現在の18関係はこの制約を満たす。

## 7. 18通りの初期関係

読み取り専用集計で得た初期関係は次のとおりである。`transactions.csv` に実在する関係を初期状態の正として採用し、この18件には人による妥当性承認を必須としない。Phase 3-12では新CSVへ書き込まず、Phase 3-13で初回ブートストラップする。

| account_code | account_name（確認用） | sub_code | sub_name |
| --- | --- | --- | --- |
| `114` | 普通預金 | `1` | 埼玉りそな東松山 |
| `114` | 普通預金 | `2` | 埼玉りそなＳＴ |
| `114` | 普通預金 | `3` | 三菱東京ＵＦＪ |
| `114` | 普通預金 | `6` | さいしん |
| `114` | 普通預金 | `8` | 埼玉りそな駅前 |
| `114` | 普通預金 | `9` | 埼玉りそな大宮 |
| `208` | 預り金 | `1` | 社会保険料 |
| `208` | 預り金 | `3` | 源泉税 |
| `208` | 預り金 | `4` | 住民税 |
| `208` | 預り金 | `6` | その他 |
| `209` | 従業員預り金 | `6` | 保険その他１ |
| `220` | 長期借入金 | `1` | 埼玉りそな |
| `220` | 長期借入金 | `2` | 日本政策金融公庫 |
| `221` | 預り保証金 | `1` | ＳＴビル |
| `527` | 租税公課 | `3` | 印紙 |
| `527` | 租税公課 | `5` | 車両関係税 |
| `800` | 法人税･住民税･事業税 | `10` | 法人税,住民税,事業税 |
| `800` | 法人税･住民税･事業税 | `20` | 源泉所得税 |

### 初回ブートストラップ手順

1. `transactions.csv` を `utf-8-sig`、全列文字列として読み取る。
2. 借方と貸方を別レコードへ展開し、`account_code`, `account_name`, `sub_code`, `sub_name` をtrimする。
3. 補助コード・名称が両方空なら除外し、片側だけなら候補にせずエラー一覧へ出す。
4. `(account_code, sub_code, sub_name)` を重複排除する。
5. `(account_code, sub_code)` ごとの名称が1種類であることを検査する。
6. 科目コード・名称を `account_master.csv`、補助コード・名称を `sub_master.csv` と照合する。
7. 検査をすべて通った18関係を、決定的な順序で初期マスターへ採用する。
8. この抽出は新ファイルが存在しない初回だけ実行し、既存の関係マスターを再生成・同期・上書きしない。

`account_name` は照合用であり、B-2の正式CSV列には含めない。科目名称の正本は `account_master.csv` とする。

初期生成後に現在名称を変更しても、過去DBから再生成して古い名称へ戻してはならない。恒久的な `transactions.csv -> sub_account_relations.csv` 自動同期処理は作らない。

## 8. FastAPI masters統合案

### 案1: 関係行をそのまま追加

```json
{
  "accounts": [],
  "sub_accounts": [],
  "departments": [],
  "sub_account_relations": [
    {
      "account_code": "114",
      "sub_code": "1",
      "sub_name": "埼玉りそな東松山",
      "label": "1　埼玉りそな東松山"
    }
  ],
  "diagnostics": {}
}
```

### 案2: 科目コード別に整形

```json
{
  "sub_accounts_by_account": {
    "114": [
      {"code": "1", "name": "埼玉りそな東松山", "label": "1　埼玉りそな東松山"}
    ]
  }
}
```

### 推奨: 案1

`sub_account_relations` の配列追加を推奨する。

- CSVの1行とAPIの1要素が対応し、診断とテストが容易である。
- JSONの動的キーを避け、Pydantic・TypeScriptの型を明示できる。
- サーバー検証でも同じ配列から複合キー索引を作れる。
- Reactは取得後に `Map<accountCode, relation[]>` を一度だけ作ればよい。
- 既存の `sub_accounts` を残すため後方互換性がある。

APIでは既存フィールドを変更せず、関係配列と診断値（関係数、重複キー、存在しない科目、空の必須値）を加算する。初期生成時には既存補助ペアとの照合結果も検査する。通常運用では現在名称が `sub_master.csv` と異なることを許容し、新ファイル読込不能や構造不正を黙って空配列へ変換してはならない。

## 9. `prepare-registration` の将来検証

借方・貸方ごとに次の順で判定する。既存の科目検証を先に通し、補助がコード・名称とも空なら「補助なし」として正常とする。

| 状態 | 判定 |
| --- | --- |
| 補助コード・名称がともに空 | 正常（補助なし） |
| 片方だけ入力 | 現行どおり入力ペア不正エラー |
| `(account_code, sub_code)` が現在の関係マスターに存在しない | 親子関係不存在エラー。必要に応じて補助コード自体の不存在も区別する |
| 親子関係は存在するが入力名称が関係マスターの現在名称と異なる | 補助名称不一致エラー |
| 親子関係と現在名称が完全一致 | 正常 |

親子関係導入後は、現行の「親科目を検証できない」warningを廃止し、関係不存在をwarningではなく登録準備を止めるerrorにする。補助コード重複warningは、完全な親子関係で一意に解決できるため不要になる。ただし関係マスター自体に重複・矛盾があれば、個別入力以前にマスター読込エラーとして処理を止める。

サーバー側を最終判定元とし、Reactの絞り込みだけに整合性を依存させない。

## 10. React補助selectのデータフロー

1. `GET /api/journal/masters` で既存3マスターと `sub_account_relations` を取得する。
2. Reactで関係配列を `account_code` ごとに索引化する。
3. 借方補助は `debitAccountCode`、貸方補助は `creditAccountCode` に対応する関係だけを表示する。
4. optionは `(account_code, sub_code)` で識別し、表示は `sub_code + 現在のsub_name`、選択時はコードと現在名称を同時更新する。
5. 「補助なし」はコード・名称を両方空にする。
6. 科目変更時はPhase 3-9どおり、その側の既存補助をクリアし、反対側には触れない。
7. 過去候補と現在マスターで `(account_code, sub_code)` が一致し名称だけが異なる場合、同じ補助として認識する。過去DBや検索候補は変更せず、新規登録用フォームでは現在名称を明示して使用できるようにする。
8. `(account_code, sub_code)` 自体が現在マスターにない過去候補は、不一致として表示して選び直しを促す。
9. 候補変更・リセット時は候補由来の初期値と警告状態を再構築する。
10. 送信DTOの `debit_sub_code/name`、`credit_sub_code/name` は維持し、サーバーで再検証する。

関係が0件の科目では「補助なし」だけを選択可能にする。マスター取得失敗時は自由入力へフォールバックせず、selectと登録準備を無効化する。

## 11. 既存機能を変更しない導入境界

新マスターの利用先を `journal_master_service`、`journal_registration_service`、FastAPI DTO、Reactに限定すれば、次の既存機能は不変にできる。

- 検索: `engine.load_data()` と `engine.search()` は引き続き `transactions.csv` だけを正解DBとして使う。score、順位、pattern、候補、`editable_rows`、`block_rows` を変えない。
- 未収消込: `receivable_engine.py` のFIFO、残高、差額、履歴、生成仕訳を変えない。Streamlit経由の現行コード化も維持する。
- Epson CSV: 45列、列順、既存の名称からコードへの変換、保存と `transactions.csv` 更新タイミングを変えない。
- Excel: 入力仕訳帳と未収確認表の列・表示値・生成処理を変えない。
- Streamlit: `SUB_MASTER` の名称一覧と `name -> code` 辞書、補助マスター生成を変えない。新関係マスターを読み込ませない。
- AI/OCR: 新マスターをプロンプト、抽出、候補生成へ渡さない。

新ファイルの追加は加算的変更であり、既存ファイルの列や内容を変えない。導入時にも検索エンジンやStreamlitから新ローダーをimportしないことを境界テストで確認する。

## 12. 将来導入時の推奨順序

1. `transactions.csv` の18関係を初回だけ抽出し、矛盾・欠落・既存2マスターとの参照整合性を機械検査する。
2. 検査済みB-2 CSVを初期作成し、以後は過去DBから再生成しない。
3. `journal_master_service` に読込と診断を加え、APIへ `sub_account_relations` を加算する。
4. `prepare-registration` にサーバー側の厳格な親子関係検証を加える。
5. React補助selectを実装する。
6. 検索・未収・Epson・Excel・Streamlitの非回帰を確認する。

各段階で `data/sub_master.csv` と `data/transactions.csv` のハッシュを確認し、既存ファイルの変更をコミットへ含めない。

## 13. Phase 3-12の設計範囲

行ったこと:

- リポジトリ内の全参照調査
- 既存CSVの読み取り専用集計
- B-1/B-2、キー、移行、API、登録検証、Reactデータフローの設計
- 本設計書の新規作成

行わなかったこと:

- `sub_account_relations.csv` の作成
- `frontend/`、`src/`、`data/`、`config/` の変更
- 検索、未収消込、CSV、Excel、AI、OCRの変更
