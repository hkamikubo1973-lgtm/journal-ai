import { useEffect, useRef, useState } from "react";
import { executeReceivableImport, previewReceivableImport } from "../../api/receivable";
import type { JournalMastersResponse } from "../../types/journal";
import type { ReceivableImportInput, ReceivableImportPreview } from "../../types/receivable";

type Props = {
  masters: JournalMastersResponse | null;
  disabled: boolean;
  onImported: () => Promise<void>;
};

export default function ReceivableImport({ masters, disabled, onImported }: Props) {
  const [input, setInput] = useState<ReceivableImportInput | null>(null);
  const [invoiceDate, setInvoiceDate] = useState("");
  const [account, setAccount] = useState<string | null>(null);
  const [department, setDepartment] = useState("");
  const [specifyDue, setSpecifyDue] = useState(false);
  const [dueDate, setDueDate] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<ReceivableImportPreview | null>(null);
  const [loading, setLoading] = useState(false);
  const [executing, setExecuting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const fileControl = useRef<HTMLInputElement>(null);
  const generation = useRef(0);
  const inFlight = useRef(false);
  const defaultAccount = account ?? (masters?.accounts.some((item) => item.name === "未収運賃")
    ? "未収運賃" : masters?.accounts[0]?.name ?? "");

  function invalidate() {
    generation.current += 1;
    setInput(null);
    setPreview(null);
    setError(null);
    setSuccess(null);
  }

  useEffect(() => {
    const requestId = ++generation.current;
    setInput(null);
    setPreview(null);
    setError(null);
    setLoading(false);
    if (!file || !invoiceDate || !defaultAccount || (specifyDue && !dueDate) || disabled) return;
    const request: ReceivableImportInput = {
      file, invoice_date: invoiceDate, default_account: defaultAccount, department,
      payment_due_date: specifyDue ? dueDate : undefined,
    };
    setLoading(true);
    void previewReceivableImport(request).then((result) => {
      if (generation.current !== requestId) return;
      setPreview(result);
      setInput(request);
    }).catch((caught: unknown) => {
      if (generation.current === requestId) setError(caught instanceof Error ? caught.message : "Excelを読み込めませんでした。");
    }).finally(() => {
      if (generation.current === requestId) setLoading(false);
    });
    return () => { generation.current += 1; };
  }, [file, invoiceDate, defaultAccount, department, specifyDue, dueDate, disabled]);

  async function execute() {
    if (!input || !preview?.importable_count || loading || disabled || inFlight.current) return;
    inFlight.current = true;
    setExecuting(true);
    setError(null);
    setSuccess(null);
    try {
      const result = await executeReceivableImport(input);
      generation.current += 1;
      setInput(null);
      setPreview(null);
      setFile(null);
      setInvoiceDate("");
      setAccount(null);
      setDepartment("");
      setSpecifyDue(false);
      setDueDate("");
      if (fileControl.current) fileControl.current.value = "";
      setSuccess(`${result.message}（対象外${result.excluded_count}件、うち重複${result.duplicate_count}件）`);
      await onImported();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "取込処理に失敗しました。未収一覧を再読込して確認してください。");
    } finally {
      inFlight.current = false;
      setExecuting(false);
    }
  }

  return <details className="receivable-panel receivable-import">
    <summary>未収Excel取込</summary>
    <fieldset disabled={disabled || executing}>
      <label>請求一覧Excel<input ref={fileControl} type="file" accept=".xlsx,.xls" onChange={(event) => { invalidate(); setFile(event.target.files?.[0] ?? null); }} /></label>
      <label>請求日<input type="date" value={invoiceDate} onChange={(event) => { invalidate(); setInvoiceDate(event.target.value); }} /></label>
      <label>既定の未収科目<select value={defaultAccount} onChange={(event) => { invalidate(); setAccount(event.target.value); }}>
        {!masters && <option value="">マスターを読み込んでください</option>}
        {masters?.accounts.map((item) => <option key={item.code} value={item.name}>{item.name}</option>)}
      </select></label>
      <label>部門<select value={department} onChange={(event) => { invalidate(); setDepartment(event.target.value); }}>
        <option value="">指定なし</option>
        {masters?.departments.map((item) => <option key={item.code} value={item.name}>{item.name}</option>)}
      </select></label>
      <label className="receivable-import-due-toggle"><input type="checkbox" checked={specifyDue} onChange={(event) => { invalidate(); setSpecifyDue(event.target.checked); }} /><span>入金予定日を指定する</span></label>
      {specifyDue ? <label>入金予定日<input type="date" value={dueDate} onChange={(event) => { invalidate(); setDueDate(event.target.value); }} /></label>
        : <p>入金予定日は請求日の翌月末で設定します。</p>}
    </fieldset>
    <p>ファイルと必須条件を指定すると、取込プレビューを表示します。</p>
    {loading && <p role="status">Excelを読み込み中…</p>}
    {error && <p className="error-message" role="alert">{error}</p>}
    {success && <p role="status">{success}</p>}
    {preview && <>
      <dl className="receivable-import-summary">
        <div><dt>読込シート</dt><dd>{preview.sheet_name}</dd></div>
        <div><dt>取込予定</dt><dd>{preview.importable_count}件</dd></div>
        <div><dt>対象外</dt><dd>{preview.excluded_count}件</dd></div>
        <div><dt>うち重複</dt><dd>{preview.duplicate_count}件</dd></div>
      </dl>
      {preview.exclusions.length > 0 && <ul>{preview.exclusions.map((item, index) => <li key={index}>
        {item.source_row_label ? `${item.source_row_label} ${item.source_row}: ` : ""}
        {String(item.values["コード"] ?? "")} {String(item.values["得意先名１"] ?? item.values["得意先名"] ?? item.values["取引先"] ?? "")}: {item.reason}
      </li>)}</ul>}
      <div className="receivable-result-scroll receivable-import-table" tabIndex={0} role="region" aria-label="未収Excel取込プレビュー"><table>
        <thead><tr>{Object.keys(preview.valid_rows[0] ?? {}).map((key) => <th key={key}>{key}</th>)}</tr></thead>
        <tbody>{preview.valid_rows.map((row, index) => <tr key={index}>{Object.entries(row).map(([key, value]) => <td key={key}>{value}</td>)}</tr>)}</tbody>
      </table></div>
      {!preview.importable_count && <p>取り込める明細がありません</p>}
      <button type="button" onClick={() => void execute()} disabled={!preview.importable_count || loading || executing || disabled}>
        {executing ? "取込中…" : "未収一覧へ取り込む"}
      </button>
    </>}
  </details>;
}
