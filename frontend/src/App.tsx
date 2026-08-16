import { useState, type FormEvent } from "react";
import { prepareRegistration, searchJournals } from "./api/journal";
import type {
  JournalCandidate,
  JournalEditForm,
  JournalSearchRequest,
  JournalSearchResponse,
  PrepareRegistrationRequest,
  PrepareRegistrationResponse,
} from "./types/journal";

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
];
const creditFields: EditFormField[] = [
  { key: "creditAccountCode", label: "貸方科目コード" }, { key: "creditAccountName", label: "貸方科目名" },
  { key: "creditSubCode", label: "貸方補助コード" }, { key: "creditSubName", label: "貸方補助名" },
  { key: "creditDeptCode", label: "貸方部門コード" }, { key: "creditDeptName", label: "貸方部門名" },
];
const amountSummaryFields: EditFormField[] = [
  { key: "amount", label: "金額", amount: true },
  { key: "summary", label: "摘要", wide: true },
];

function getString(row: Record<string, unknown>, key: string): string {
  const value = row[key];
  if (value === null || value === undefined) return "";
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}

function buildEditFormFromRow(row: Record<string, unknown>): JournalEditForm {
  const debitAmount = getString(row, "借方金額");
  const creditAmount = getString(row, "貸方金額");
  return {
    voucherDate: getString(row, "伝票日付"), voucherNo: getString(row, "証番号"),
    voucherSummary: getString(row, "伝票摘要"), debitAccountCode: getString(row, "借方科目"),
    debitAccountName: getString(row, "借方科目名"), debitSubCode: getString(row, "借方補助"),
    debitSubName: getString(row, "借方補助科目名"), debitDeptCode: getString(row, "借方部門"),
    debitDeptName: getString(row, "借方部門名"), amount: chooseCommonAmount(debitAmount, creditAmount),
    debitAmount,
    creditAccountCode: getString(row, "貸方科目"), creditAccountName: getString(row, "貸方科目名"),
    creditSubCode: getString(row, "貸方補助"), creditSubName: getString(row, "貸方補助科目名"),
    creditDeptCode: getString(row, "貸方部門"), creditDeptName: getString(row, "貸方部門名"),
    creditAmount, summary: getString(row, "摘要"),
  };
}

function chooseCommonAmount(debitAmount: string, creditAmount: string): string {
  return debitAmount || creditAmount || "";
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

function areSourceAmountsEqual(form: JournalEditForm | null): boolean | null {
  if (!form || !form.debitAmount || !form.creditAmount) return null;
  const debitAmount = parseAmount(form.debitAmount);
  const creditAmount = parseAmount(form.creditAmount);
  if (debitAmount === null || creditAmount === null) return null;
  return debitAmount === creditAmount;
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

function isEditFormChanged(
  current: JournalEditForm | null,
  initial: JournalEditForm | null,
): boolean {
  if (!current || !initial) return false;
  return JSON.stringify(current) !== JSON.stringify(initial);
}

function isFieldChanged(
  field: keyof JournalEditForm,
  current: JournalEditForm,
  initial: JournalEditForm | null,
): boolean {
  return initial !== null && current[field] !== initial[field];
}

function CandidateCard({ candidate, selected, onSelect }: {
  candidate: JournalCandidate; selected: boolean; onSelect: (candidate: JournalCandidate) => void;
}) {
  const journal = getCandidateSummary(candidate);

  return (
    <article className={`candidate-card clickable${selected ? " selected" : ""}`}
      aria-current={selected ? "true" : undefined} onClick={() => onSelect(candidate)}>
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
      <button className="candidate-select-button" type="button" onClick={(event) => {
        event.stopPropagation();
        onSelect(candidate);
      }}>
        {selected ? "選択中の候補" : "この候補を選択"}
      </button>
    </article>
  );
}

function FormSection({ title, fields, editForm, initialEditForm, onChange, className = "", gridClassName = "" }: {
  title: string; fields: EditFormField[]; editForm: JournalEditForm;
  initialEditForm: JournalEditForm | null;
  onChange: (key: keyof JournalEditForm, value: string) => void;
  className?: string;
  gridClassName?: string;
}) {
  return (
    <fieldset className={`form-section ${className}`.trim()}><legend>{title}</legend><div className={`form-grid ${gridClassName}`.trim()}>
      {fields.map((field) => {
        const changed = isFieldChanged(field.key, editForm, initialEditForm);
        const fieldClassName = [field.wide ? "form-field-wide" : "", changed ? "field-changed" : ""].filter(Boolean).join(" ");
        const controlClassName = [field.wide ? "summary-textarea" : "", field.amount ? "amount-input" : "", changed ? "changed-field" : ""].filter(Boolean).join(" ");
        return (
          <label className={fieldClassName || undefined} key={field.key}>{field.label}
            {field.wide ? (
              <textarea className={controlClassName} value={editForm[field.key]}
                onChange={(event) => onChange(field.key, event.target.value)} rows={3} />
            ) : (
              <input className={controlClassName || undefined} inputMode={field.amount ? "numeric" : undefined}
                value={editForm[field.key]} onChange={(event) => onChange(field.key, event.target.value)} />
            )}
          </label>
        );
      })}
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
      ) : <p className="muted">同一伝票ブロック情報はありません。</p>}
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
  const [initialEditForm, setInitialEditForm] = useState<JournalEditForm | null>(null);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [prepareLoading, setPrepareLoading] = useState(false);
  const [prepareResponse, setPrepareResponse] = useState<PrepareRegistrationResponse | null>(null);
  const [prepareError, setPrepareError] = useState<string | null>(null);
  const [prepareStatusMessage, setPrepareStatusMessage] = useState<string | null>(null);
  const [registrationCart, setRegistrationCart] = useState<PrepareRegistrationResponse[]>([]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLoading(true);
    setError(null);
    setStatusMessage(null);
    setSelectedCandidate(null);
    setEditForm(null);
    setInitialEditForm(null);
    setPrepareResponse(null);
    setPrepareError(null);
    setPrepareStatusMessage(null);
    const request: JournalSearchRequest = {
      keyword, department: department.trim() || null, amount: amount === "" ? null : Number(amount), limit,
    };
    try {
      setResult(await searchJournals(request));
      setStatusMessage("検索結果を更新しました。候補を選択してください。");
    }
    catch (caughtError) {
      setResult(null);
      setError(caughtError instanceof Error ? caughtError.message : "検索中に不明なエラーが発生しました。");
    } finally { setLoading(false); }
  }

  function handleCandidateSelect(candidate: JournalCandidate) {
    setSelectedCandidate(candidate);
    setStatusMessage(null);
    setPrepareResponse(null);
    setPrepareError(null);
    setPrepareStatusMessage(null);
    const firstEditableRow = candidate.editable_rows[0];
    const nextEditForm = firstEditableRow ? buildEditFormFromRow(firstEditableRow) : null;
    setEditForm(nextEditForm ? { ...nextEditForm } : null);
    setInitialEditForm(nextEditForm ? { ...nextEditForm } : null);
  }

  function updateEditForm(key: keyof JournalEditForm, value: string) {
    setEditForm((current) => current ? { ...current, [key]: value } : current);
    setPrepareResponse(null);
    setPrepareError(null);
    setPrepareStatusMessage(null);
  }

  function resetEditForm() {
    if (initialEditForm) {
      setEditForm({ ...initialEditForm });
      setPrepareResponse(null);
      setPrepareError(null);
      setPrepareStatusMessage(null);
    }
  }

  async function handlePrepareRegistration() {
    if (!selectedCandidate || !editForm) return;

    const nullable = (value: string) => value.trim() || null;
    const request: PrepareRegistrationRequest = {
      edit_form: {
        voucher_date: editForm.voucherDate,
        voucher_no: nullable(editForm.voucherNo),
        voucher_summary: nullable(editForm.voucherSummary),
        debit_account_code: editForm.debitAccountCode,
        debit_account_name: nullable(editForm.debitAccountName),
        debit_sub_code: nullable(editForm.debitSubCode),
        debit_sub_name: nullable(editForm.debitSubName),
        debit_dept_code: nullable(editForm.debitDeptCode),
        debit_dept_name: nullable(editForm.debitDeptName),
        credit_account_code: editForm.creditAccountCode,
        credit_account_name: nullable(editForm.creditAccountName),
        credit_sub_code: nullable(editForm.creditSubCode),
        credit_sub_name: nullable(editForm.creditSubName),
        credit_dept_code: nullable(editForm.creditDeptCode),
        credit_dept_name: nullable(editForm.creditDeptName),
        amount: editForm.amount,
        summary: nullable(editForm.summary),
        source_debit_amount: nullable(editForm.debitAmount),
        source_credit_amount: nullable(editForm.creditAmount),
      },
      candidate_meta: {
        rank: selectedCandidate.rank,
        score: selectedCandidate.score,
        pattern_key: selectedCandidate.pattern_key,
        pattern_rank: selectedCandidate.pattern_rank,
        editable_row_count: selectedCandidate.editable_rows.length,
        source_row_count: selectedCandidate.source_rows.length,
        block_row_count: selectedCandidate.block_rows.length,
        has_fukugo: selectedCandidate.has_fukugo,
        has_sundry: selectedCandidate.has_sundry,
        contains_fukugo_or_sundry: selectedCandidate.contains_fukugo_or_sundry,
        show_block_rows: selectedCandidate.show_block_rows,
        is_complex: selectedCandidate.is_complex,
      },
    };

    setPrepareLoading(true);
    setPrepareError(null);
    setPrepareStatusMessage(null);
    try {
      const response = await prepareRegistration(request);
      setPrepareResponse(response);
      if (response.ok && response.registration_id && response.prepared_journal && response.epson_preview_row) {
        setRegistrationCart((current) => {
          if (current.some((item) => item.registration_id === response.registration_id)) {
            setPrepareStatusMessage("同じ登録予定はすでにカートへ追加されています。");
            return current;
          }
          setPrepareStatusMessage("登録予定へ追加しました（画面上の確認のみ）。");
          return [...current, response];
        });
      }
    } catch (caughtError) {
      setPrepareResponse(null);
      setPrepareError(caughtError instanceof Error ? caughtError.message : "登録準備中に不明なエラーが発生しました。");
    } finally {
      setPrepareLoading(false);
    }
  }

  const selectedCandidateIsComplex = Boolean(selectedCandidate && (
    selectedCandidate.has_fukugo || selectedCandidate.has_sundry || selectedCandidate.contains_fukugo_or_sundry ||
    selectedCandidate.show_block_rows || selectedCandidate.is_complex
  ));
  const sourceAmountsEqual = areSourceAmountsEqual(editForm);
  const editFormChanged = isEditFormChanged(editForm, initialEditForm);
  const selectedSummary = selectedCandidate ? getCandidateSummary(selectedCandidate) : null;

  return (
    <main className="app-shell">
      <header className="page-header">
        <p className="eyebrow">Journal workflow prototype</p>
        <h1>journal-ai 正式UI Phase 3-1 登録準備</h1>
        <p>通常1行仕訳を検証・整形し、画面上の登録予定へ追加します。保存やDB登録はまだ行いません。</p>
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
            {statusMessage && <p className="status-message">{statusMessage}</p>}
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
              {selectedSummary && <div className="selection-summary">
                <strong>{selectedSummary.debit} <span aria-hidden="true">→</span> {selectedSummary.credit}</strong>
                <b>{selectedSummary.amount}</b>
                <span>{selectedSummary.summary}</span>
              </div>}
              <small>pattern_key: {selectedCandidate.pattern_key.join(" / ") || "-"}</small>
            </div>
            <div className={`edit-status ${editFormChanged ? "changed" : "unchanged"}`}>
              <strong>{editFormChanged ? "編集状態：画面上で変更あり（未保存）" : "編集状態：未変更"}</strong>
              <span>変更内容は保存されません。</span>
            </div>
            {selectedCandidate.editable_rows.length === 0 && <p className="notice notice-error">この候補には編集用行がありません。</p>}
            {selectedCandidate.editable_rows.length > 1 && <p className="notice notice-warning">この候補は複数行の編集候補です。今回は通常1行仕訳向けの確認として先頭行のみ表示しています。</p>}
            {selectedCandidateIsComplex && <p className="notice notice-warning">
              この候補は資金複合または諸口を含む可能性があります。block_rows を確認し、登録時は実際の相手科目へ修正してください。
            </p>}
            {sourceAmountsEqual === false && <p className="notice notice-warning">
              元データの借方金額と貸方金額が一致していません。内容を確認してください。
            </p>}
            {editForm && <form className="edit-form" onSubmit={(event) => event.preventDefault()}>
              <FormSection title="基本情報" fields={basicFields} editForm={editForm} initialEditForm={initialEditForm} onChange={updateEditForm}
                gridClassName="basic-info-grid" />
              <div className="debit-credit-grid">
                <FormSection title="借方" fields={debitFields} editForm={editForm} initialEditForm={initialEditForm} onChange={updateEditForm}
                  className="side-section debit" gridClassName="side-form-grid" />
                <FormSection title="貸方" fields={creditFields} editForm={editForm} initialEditForm={initialEditForm} onChange={updateEditForm}
                  className="side-section credit" gridClassName="side-form-grid" />
              </div>
              <FormSection title="金額・摘要" fields={amountSummaryFields} editForm={editForm} initialEditForm={initialEditForm}
                onChange={updateEditForm} className="single-amount-section" gridClassName="amount-summary-grid" />
              <div className="amount-summary-panel">
                <strong>{editForm.amount ? `入力金額：${formatAmountWithUnit(editForm.amount)}` : "入力金額：未入力"}</strong>
                <span>出力想定：借方金額・貸方金額へ同額反映</span>
                <small>元データ：借方 {formatAmountWithUnit(editForm.debitAmount)} / 貸方 {formatAmountWithUnit(editForm.creditAmount)}</small>
                <p className={`source-amount-check ${sourceAmountsEqual === true ? "match" : sourceAmountsEqual === false ? "mismatch" : "incomplete"}`}>
                  {sourceAmountsEqual === true ? "元データの借貸金額は一致しています。" : sourceAmountsEqual === false ? "元データの借貸金額が一致していません。" : "元データの借貸金額は片側のみ、または未入力です。"}
                </p>
                <p className="amount-note">通常1行仕訳では、この金額を借方金額・貸方金額へ同額反映する想定です。この画面ではまだ登録・CSV出力は行いません。</p>
              </div>
              <div className="edit-form-footer">
                <p>このボタンは登録予定データをAPIで整形するだけです。まだ保存・DB登録・CSV出力は行いません。</p>
                <div className="edit-actions">
                  <button type="button" className="reset-button" onClick={resetEditForm} disabled={!editFormChanged}>候補選択時の値に戻す</button>
                  <button type="button" className="prepare-action" onClick={handlePrepareRegistration} disabled={prepareLoading}>
                    {prepareLoading ? "登録準備中…" : "登録予定へ追加（確認のみ）"}
                  </button>
                </div>
              </div>
              {prepareError && <p className="prepare-result error-message">{prepareError}</p>}
              {prepareStatusMessage && <p className="prepare-result status-message">{prepareStatusMessage}</p>}
              {prepareResponse?.blocked && <div className="prepare-result prepare-blocked" role="alert">
                <strong>登録準備できませんでした</strong>
                <ul>{prepareResponse.errors.map((reason, index) => <li key={`${reason}-${index}`}>理由: {reason}</li>)}</ul>
              </div>}
              {prepareResponse?.ok && prepareResponse.warnings.length > 0 && <div className="prepare-result prepare-warning">
                {prepareResponse.warnings.map((warning, index) => <p key={`${warning}-${index}`}>{warning}</p>)}
              </div>}
            </form>}
            <BlockRowsTable candidate={selectedCandidate} />
          </>}
        </section>
      </div>

      <section className="registration-panel" aria-live="polite">
        <div className="pane-heading"><div><p className="step-label">Step 4</p><h2>出力待ちカート（画面上のみ）</h2></div>
          <span className="result-pill">{registrationCart.length}件</span>
        </div>
        <p className="registration-panel-note">画面上の一時保持です。リロードすると消えます。保存・DB登録・CSV出力は行いません。</p>
        {registrationCart.length === 0 ? <p className="cart-empty">登録予定はまだありません。</p> : <div className="cart-list">
          {registrationCart.map((item) => item.registration_id && item.prepared_journal && <article className="cart-item" key={item.registration_id}>
            <div><span>ID {item.registration_id.slice(0, 12)}…</span><strong>
              {item.prepared_journal.debit_account_name || item.prepared_journal.debit_account_code}
              <b aria-hidden="true">→</b>
              {item.prepared_journal.credit_account_name || item.prepared_journal.credit_account_code}
            </strong></div>
            <b className="cart-amount">{item.prepared_journal.amount.toLocaleString("ja-JP")}円</b>
            <p>{item.prepared_journal.summary || "摘要なし"}</p>
          </article>)}
        </div>}
      </section>

      <section className="dev-panel">
        <div className="pane-heading"><div><p className="step-label">Development</p><h2>開発確認エリア</h2></div></div>
        <details><summary>APIレスポンスJSONを表示</summary><pre>{JSON.stringify(result, null, 2)}</pre></details>
        <details><summary>編集フォームstateを表示</summary><pre>{JSON.stringify(editForm, null, 2)}</pre></details>
        <details><summary>登録準備APIレスポンスを表示</summary><pre>{JSON.stringify(prepareResponse, null, 2)}</pre></details>
      </section>
    </main>
  );
}
