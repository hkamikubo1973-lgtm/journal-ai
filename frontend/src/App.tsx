import { useState, type FormEvent } from "react";
import { searchJournals } from "./api/journal";
import type { JournalCandidate, JournalEditForm, JournalSearchRequest, JournalSearchResponse } from "./types/journal";

const blockRowFields = ["date", "debit", "credit", "debit_sub", "credit_sub", "debit_amount", "credit_amount", "summary"] as const;

type EditFormField = { key: keyof JournalEditForm; label: string; wide?: boolean };

const basicFields: EditFormField[] = [
  { key: "voucherDate", label: "伝票日付" }, { key: "voucherNo", label: "証番号" },
  { key: "voucherSummary", label: "伝票摘要", wide: true },
];
const debitFields: EditFormField[] = [
  { key: "debitAccountCode", label: "借方科目コード" }, { key: "debitAccountName", label: "借方科目名" },
  { key: "debitSubCode", label: "借方補助コード" }, { key: "debitSubName", label: "借方補助名" },
  { key: "debitDeptCode", label: "借方部門コード" }, { key: "debitDeptName", label: "借方部門名" },
  { key: "debitAmount", label: "借方金額" },
];
const creditFields: EditFormField[] = [
  { key: "creditAccountCode", label: "貸方科目コード" }, { key: "creditAccountName", label: "貸方科目名" },
  { key: "creditSubCode", label: "貸方補助コード" }, { key: "creditSubName", label: "貸方補助名" },
  { key: "creditDeptCode", label: "貸方部門コード" }, { key: "creditDeptName", label: "貸方部門名" },
  { key: "creditAmount", label: "貸方金額" },
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

function CandidateCard({ candidate, selected, onSelect }: {
  candidate: JournalCandidate; selected: boolean; onSelect: (candidate: JournalCandidate) => void;
}) {
  return (
    <article className={`candidate-card${selected ? " selected" : ""}`} aria-current={selected ? "true" : undefined}>
      <div className="candidate-card-heading">
        <h3>候補{candidate.rank} <span>/ Score {candidate.score}</span></h3>
        {selected && <span className="selected-badge">選択中</span>}
      </div>
      <p className="pattern-key"><span>pattern_key:</span> {candidate.pattern_key.join(" / ") || "-"}</p>
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

function FormSection({ title, fields, editForm, onChange }: {
  title: string; fields: EditFormField[]; editForm: JournalEditForm;
  onChange: (key: keyof JournalEditForm, value: string) => void;
}) {
  return (
    <fieldset className="form-section"><legend>{title}</legend><div className="form-grid">
      {fields.map((field) => (
        <label className={field.wide ? "form-field-wide" : undefined} key={field.key}>{field.label}
          {field.wide ? (
            <textarea value={editForm[field.key]} onChange={(event) => onChange(field.key, event.target.value)} rows={3} />
          ) : (
            <input value={editForm[field.key]} onChange={(event) => onChange(field.key, event.target.value)} />
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
          {blockRowFields.map((field) => <th key={field}>{field}</th>)}
        </tr></thead><tbody>
          {candidate.block_rows.slice(0, 5).map((row, index) => <tr key={index}>
            {blockRowFields.map((field) => <td key={field}>{displayValue(row[field])}</td>)}
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

  return (
    <main className="app-shell">
      <header className="page-header">
        <p className="eyebrow">Journal workflow prototype</p>
        <h1>journal-ai 正式UI Phase 2-3 Split-View試作</h1>
        <p>通常仕訳検索APIの候補DTOを、正式UIに近い左右分割レイアウトで確認します。</p>
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
            <div className="selected-candidate-header"><div><span>候補{selectedCandidate.rank}を編集中</span>
              <strong>Score {selectedCandidate.score}</strong></div>
              <p>pattern_key: {selectedCandidate.pattern_key.join(" / ") || "-"}</p>
            </div>
            {selectedCandidate.editable_rows.length === 0 && <p className="notice notice-error">この候補には編集用行がありません。</p>}
            {selectedCandidate.editable_rows.length > 1 && <p className="notice notice-warning">この候補は複数行の編集候補です。今回は先頭行のみ表示しています。</p>}
            {selectedCandidateIsComplex && <p className="notice notice-warning">
              この候補は資金複合または諸口を含む可能性があります。block_rows を確認し、登録時は実際の相手科目へ修正してください。
            </p>}
            {editForm && <form className="edit-form" onSubmit={(event) => event.preventDefault()}>
              <FormSection title="基本情報" fields={basicFields} editForm={editForm} onChange={updateEditForm} />
              <FormSection title="借方" fields={debitFields} editForm={editForm} onChange={updateEditForm} />
              <FormSection title="貸方" fields={creditFields} editForm={editForm} onChange={updateEditForm} />
              <FormSection title="摘要" fields={summaryFields} editForm={editForm} onChange={updateEditForm} />
              <button type="button" className="disabled-action" disabled>登録APIは未実装です</button>
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
