import { useState, type FormEvent } from "react";
import { searchJournals } from "./api/journal";
import type { JournalCandidate, JournalEditForm, JournalSearchRequest, JournalSearchResponse } from "./types/journal";

const blockRowFields = [
  { key: "date", label: "日付", amount: false },
  { key: "debit", label: "借方", amount: false },
  { key: "credit", label: "貸方", amount: false },
  { key: "debit_sub", label: "借方補助", amount: false },
  { key: "credit_sub", label: "貸方補助", amount: false },
  { key: "debit_amount", label: "借方金額", amount: true },
  { key: "credit_amount", label: "貸方金額", amount: true },
  { key: "summary", label: "摘要", amount: false },
] as const;

type EditFormField = {
  key: keyof JournalEditForm;
  label: string;
  wide?: boolean;
  amount?: boolean;
};

const basicFields: EditFormField[] = [
  { key: "voucherDate", label: "伝票日付" }, { key: "voucherNo", label: "証番号" },
  { key: "voucherSummary", label: "伝票摘要", wide: true },
];
const debitFields: EditFormField[] = [
  { key: "debitAccountCode", label: "借方科目コード" }, { key: "debitAccountName", label: "借方科目名" },
  { key: "debitSubCode", label: "借方補助コード" }, { key: "debitSubName", label: "借方補助名" },
  { key: "debitDeptCode", label: "借方部門コード" }, { key: "debitDeptName", label: "借方部門名" },
  { key: "debitAmount", label: "借方金額", amount: true },
];
const creditFields: EditFormField[] = [
  { key: "creditAccountCode", label: "貸方科目コード" }, { key: "creditAccountName", label: "貸方科目名" },
  { key: "creditSubCode", label: "貸方補助コード" }, { key: "creditSubName", label: "貸方補助名" },
  { key: "creditDeptCode", label: "貸方部門コード" }, { key: "creditDeptName", label: "貸方部門名" },
  { key: "creditAmount", label: "貸方金額", amount: true },
];
const summaryFields: EditFormField[] = [{ key: "summary", label: "摘要", wide: true }];

function getString(row: Record<string, unknown>, key: string): string {
  const value = row[key];
  if (value === null || value === undefined) return "";
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}

function buildEditFormFromRow(row: Record<string, unknown>): JournalEditForm {
  return {
    voucherDate: getString(row, "伝票日付"), voucherNo: getString(row, "証番号"),
    voucherSummary: getString(row, "伝票摘要"), debitAccountCode: getString(row, "借方科目"),
    debitAccountName: getString(row, "借方科目名"), debitSubCode: getString(row, "借方補助"),
    debitSubName: getString(row, "借方補助科目名"), debitDeptCode: getString(row, "借方部門"),
    debitDeptName: getString(row, "借方部門名"), debitAmount: getString(row, "借方金額"),
    creditAccountCode: getString(row, "貸方科目"), creditAccountName: getString(row, "貸方科目名"),
    creditSubCode: getString(row, "貸方補助"), creditSubName: getString(row, "貸方補助科目名"),
    creditDeptCode: getString(row, "貸方部門"), creditDeptName: getString(row, "貸方部門名"),
    creditAmount: getString(row, "貸方金額"), summary: getString(row, "摘要"),
  };
}

function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "-";
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}

function parseAmount(value: string): number | null {
  const normalized = value.replace(/[,\s円]/g, "");
  if (normalized === "") return null;
  const amount = Number(normalized);
  return Number.isFinite(amount) ? amount : null;
}

function formatAmount(value: string): string {
  const amount = parseAmount(value);
  return amount === null ? (value || "-") : amount.toLocaleString("ja-JP");
}

function formatAmountWithUnit(value: string): string {
  const formatted = formatAmount(value);
  return formatted === "-" ? formatted : `${formatted}円`;
}

function getCandidateSummary(candidate: JournalCandidate) {
  const editableRow = candidate.editable_rows[0] ?? {};
  const blockRow = candidate.block_rows[0] ?? {};
  const getValue = (editableKey: string, blockKey: string) =>
    getString(editableRow, editableKey) || getString(blockRow, blockKey);
  const debitAmount = getValue("借方金額", "debit_amount");
  const creditAmount = getValue("貸方金額", "credit_amount");

  return {
    debit: getValue("借方科目名", "debit") || "-",
    credit: getValue("貸方科目名", "credit") || "-",
    amount: formatAmountWithUnit(debitAmount || creditAmount),
    summary: getValue("摘要", "summary") || "-",
  };
}

function CandidateCard({ candidate, selected, onSelect }: {
  candidate: JournalCandidate; selected: boolean; onSelect: (candidate: JournalCandidate) => void;
}) {
  const journal = getCandidateSummary(candidate);

  return (
    <article className={`candidate-card${selected ? " selected" : ""}`} aria-current={selected ? "true" : undefined}>
      <div className="candidate-card-heading">
        <h3>候補{candidate.rank} <span>/ Score {candidate.score}</span></h3>
        {selected && <span className="selected-badge">選択中</span>}
      </div>
      <p className="pattern-key"><span>pattern_key:</span> {candidate.pattern_key.join(" / ") || "-"}</p>
      <p className="candidate-journal-line">
        <strong>{journal.debit}</strong>
        <span aria-hidden="true">→</span>
        <strong>{journal.credit}</strong>
        <b>{journal.amount}</b>
      </p>
      <p className="candidate-summary-line" title={journal.summary}>{journal.summary}</p>
      <dl className="candidate-facts">
        <div><dt>source / edit / block</dt><dd>{candidate.source_rows.length} / {candidate.editable_rows.length} / {candidate.block_rows.length}</dd></div>
        <div><dt>資金複合</dt><dd>{candidate.has_fukugo ? "あり" : "なし"}</dd></div>
        <div><dt>諸口</dt><dd>{candidate.has_sundry ? "あり" : "なし"}</dd></div>
      </dl>
      {candidate.search_reason.length > 0 && (
        <div className="search-reasons"><span>検索理由</span><ul>
          {candidate.search_reason.slice(0, 2).map((reason, index) => <li key={`${reason}-${index}`}>{reason}</li>)}
        </ul></div>
      )}
      <button className="candidate-select-button" type="button" onClick={() => onSelect(candidate)}>
        {selected ? "選択中の候補" : "この候補を選択"}
      </button>
    </article>
  );
}

function FormSection({ title, fields, editForm, onChange, className = "", gridClassName = "" }: {
  title: string; fields: EditFormField[]; editForm: JournalEditForm;
  onChange: (key: keyof JournalEditForm, value: string) => void;
  className?: string;
  gridClassName?: string;
}) {
  return (
    <fieldset className={`form-section ${className}`.trim()}><legend>{title}</legend><div className={`form-grid ${gridClassName}`.trim()}>
      {fields.map((field) => (
        <label className={field.wide ? "form-field-wide" : undefined} key={field.key}>{field.label}
          {field.wide ? (
            <textarea className="summary-textarea" value={editForm[field.key]} onChange={(event) => onChange(field.key, event.target.value)} rows={3} />
          ) : (
            <input className={field.amount ? "amount-input" : undefined} inputMode={field.amount ? "numeric" : undefined}
              value={editForm[field.key]} onChange={(event) => onChange(field.key, event.target.value)} />
          )}
        </label>
      ))}
    </div></fieldset>
  );
}

function BlockRowsTable({ candidate }: { candidate: JournalCandidate }) {
  return (
    <section className="block-panel">
      <div className="section-heading-row"><h3>同一伝票ブロック（参照用）</h3><span>block_rows: {candidate.block_rows.length}件</span></div>
      {candidate.block_rows.length > 0 ? (
        <div className="table-scroll"><table><thead><tr>
          {blockRowFields.map((field) => <th key={field.key}>{field.label}</th>)}
        </tr></thead><tbody>
          {candidate.block_rows.slice(0, 5).map((row, index) => <tr key={index}>
            {blockRowFields.map((field) => <td className={field.amount ? "table-amount" : undefined} key={field.key}>
              {field.amount ? formatAmount(getString(row, field.key)) : displayValue(row[field.key])}
            </td>)}
          </tr>)}
        </tbody></table></div>
      ) : <p className="muted">参照用のblock_rowsはありません。</p>}
    </section>
  );
}

export default function App() {
  const [keyword, setKeyword] = useState("りそな銀行");
  const [department, setDepartment] = useState("");
  const [amount, setAmount] = useState("");
  const [limit, setLimit] = useState<5 | 10 | 20>(5);
  const [result, setResult] = useState<JournalSearchResponse | null>(null);
  const [selectedCandidate, setSelectedCandidate] = useState<JournalCandidate | null>(null);
  const [editForm, setEditForm] = useState<JournalEditForm | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setLoading(true); setError(null); setSelectedCandidate(null); setEditForm(null);
    const request: JournalSearchRequest = {
      keyword, department: department.trim() || null, amount: amount === "" ? null : Number(amount), limit,
    };
    try { setResult(await searchJournals(request)); }
    catch (caughtError) {
      setResult(null);
      setError(caughtError instanceof Error ? caughtError.message : "検索中に不明なエラーが発生しました。");
    } finally { setLoading(false); }
  }

  function handleCandidateSelect(candidate: JournalCandidate) {
    setSelectedCandidate(candidate);
    const firstEditableRow = candidate.editable_rows[0];
    setEditForm(firstEditableRow ? buildEditFormFromRow(firstEditableRow) : null);
  }

  function updateEditForm(key: keyof JournalEditForm, value: string) {
    setEditForm((current) => current ? { ...current, [key]: value } : current);
  }

  const selectedCandidateIsComplex = Boolean(selectedCandidate && (
    selectedCandidate.has_fukugo || selectedCandidate.has_sundry || selectedCandidate.contains_fukugo_or_sundry ||
    selectedCandidate.show_block_rows || selectedCandidate.is_complex
  ));
  const debitAmount = editForm ? parseAmount(editForm.debitAmount) : null;
  const creditAmount = editForm ? parseAmount(editForm.creditAmount) : null;
  const amountsComparable = debitAmount !== null && creditAmount !== null;
  const amountsMatch = amountsComparable && debitAmount === creditAmount;

  return (
    <main className="app-shell">
      <header className="page-header">
        <p className="eyebrow">Journal workflow prototype</p>
        <h1>journal-ai 正式UI Phase 2-4 実務入力試作</h1>
        <p>候補DTOを展開した編集フォームの入力性・視認性・誤操作防止を確認します。</p>
      </header>

      <div className="split-layout">
        <section className="pane left-pane" aria-label="検索と候補一覧">
          <div className="search-panel">
            <div className="pane-heading"><div><p className="step-label">Step 1</p><h2>検索条件</h2></div>
              {result && <span className="result-pill">{result.count}件</span>}
            </div>
            <form className="search-form" onSubmit={handleSubmit}>
              <label className="search-keyword">キーワード<input value={keyword} onChange={(event) => setKeyword(event.target.value)} required /></label>
              <label>部門<input value={department} onChange={(event) => setDepartment(event.target.value)} placeholder="空欄可" /></label>
              <label>金額<input type="number" value={amount} onChange={(event) => setAmount(event.target.value)} placeholder="空欄可" /></label>
              <label>表示件数<select value={limit} onChange={(event) => setLimit(Number(event.target.value) as 5 | 10 | 20)}>
                <option value={5}>5</option><option value={10}>10</option><option value={20}>20</option>
              </select></label>
              <button type="submit" disabled={loading}>{loading ? "検索中…" : "検索"}</button>
            </form>
            {error && <p className="error-message">{error}</p>}
          </div>

          <div className="candidate-panel" aria-live="polite">
            <div className="pane-heading candidate-list-heading"><div><p className="step-label">Step 2</p><h2>候補一覧</h2></div>
              {result && <span className="muted">検索結果: {result.count}件</span>}
            </div>
            {result ? <div className="candidate-list">
              {result.candidates.map((candidate) => <CandidateCard
                key={`${candidate.rank}-${candidate.pattern_key.join("-")}`} candidate={candidate}
                selected={selectedCandidate === candidate} onSelect={handleCandidateSelect}
              />)}
            </div> : <p className="candidate-placeholder">検索すると、ここに候補が表示されます。</p>}
          </div>
        </section>

        <section className="pane right-pane" aria-live="polite">
          <div className="edit-panel-heading"><div><p className="step-label">Step 3</p><h2>選択中の仕訳編集フォーム</h2></div>
            <span className="status-badge">確認用・未登録</span>
          </div>
          {!selectedCandidate && <div className="empty-edit-state">
            <div className="empty-state-icon" aria-hidden="true">↖</div><h3>候補を選択してください</h3>
            <p>左側の候補を選択すると、editable_rows[0] の内容を編集フォームに表示します。<br />
              この画面ではまだ登録・CSV出力は行いません。</p>
          </div>}
          {selectedCandidate && <>
            <div className="status-strip">
              <div className="status-strip-main">
                <strong>選択中：候補{selectedCandidate.rank} / Score {selectedCandidate.score}</strong>
                <span className="unregistered-badge">状態：確認用・未登録</span>
              </div>
              <p>この画面ではまだ登録・CSV出力は行いません。</p>
              <small>pattern_key: {selectedCandidate.pattern_key.join(" / ") || "-"}</small>
            </div>
            {selectedCandidate.editable_rows.length === 0 && <p className="notice notice-error">この候補には編集用行がありません。</p>}
            {selectedCandidate.editable_rows.length > 1 && <p className="notice notice-warning">この候補は複数行の編集候補です。今回は先頭行のみ表示しています。</p>}
            {selectedCandidateIsComplex && <p className="notice notice-warning">
              この候補は資金複合または諸口を含む可能性があります。block_rows を確認し、登録時は実際の相手科目へ修正してください。
            </p>}
            {editForm && <form className="edit-form" onSubmit={(event) => event.preventDefault()}>
              <FormSection title="基本情報" fields={basicFields} editForm={editForm} onChange={updateEditForm}
                gridClassName="basic-info-grid" />
              <div className="debit-credit-grid">
                <FormSection title="借方" fields={debitFields} editForm={editForm} onChange={updateEditForm}
                  className="side-section debit" gridClassName="side-form-grid" />
                <FormSection title="貸方" fields={creditFields} editForm={editForm} onChange={updateEditForm}
                  className="side-section credit" gridClassName="side-form-grid" />
              </div>
              <div className={`amount-check ${amountsMatch ? "match" : amountsComparable ? "mismatch" : "incomplete"}`}>
                <strong>金額確認：借方 {formatAmountWithUnit(editForm.debitAmount)} / 貸方 {formatAmountWithUnit(editForm.creditAmount)}</strong>
                <span>{amountsMatch ? "借貸金額は一致しています。" : amountsComparable ? "借貸金額が一致していません。" : "借貸金額を入力してください。"}</span>
              </div>
              <FormSection title="摘要" fields={summaryFields} editForm={editForm} onChange={updateEditForm} />
              <div className="edit-form-footer">
                <p>編集内容は画面上の確認用stateにのみ反映されます。まだ保存・登録は行いません。</p>
                <button type="button" className="disabled-action" disabled>登録APIは未実装です</button>
              </div>
            </form>}
            <BlockRowsTable candidate={selectedCandidate} />
          </>}
        </section>
      </div>

      <section className="dev-panel">
        <div className="pane-heading"><div><p className="step-label">Development</p><h2>開発確認エリア</h2></div></div>
        <details><summary>APIレスポンスJSONを表示</summary><pre>{JSON.stringify(result, null, 2)}</pre></details>
        <details><summary>編集フォームstateを表示</summary><pre>{JSON.stringify(editForm, null, 2)}</pre></details>
      </section>
    </main>
  );
}
