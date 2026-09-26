import { useEffect, useRef, useState } from "react";
import { importJournalCsv, type JournalImportResult } from "../api/journalImport";
import type { ReactNode } from "react";
import OutputSettings from "./OutputSettings";

const columns = ["伝票日付", "借方科目", "借方科目名", "貸方科目", "貸方科目名", "借方金額", "摘要"];

export function ImportPreview({ result, busy, onExecute }: {
  result: JournalImportResult; busy: boolean; onExecute: () => void;
}) {
  return <>
    <dl className="journal-import-summary">
      <div><dt>文字コード</dt><dd>{result.detected_encoding ?? "不明"}</dd></div>
      <div><dt>列数</dt><dd>{result.column_count}</dd></div>
      <div><dt>読込件数</dt><dd>{result.uploaded_count}</dd></div>
      <div><dt>新規件数</dt><dd>{result.new_count}</dd></div>
      <div><dt>重複件数</dt><dd>{result.duplicate_count}</dd></div>
    </dl>
    {result.errors.map((error, index) => <p role="alert" className="error-message" key={index}>{error}</p>)}
    {result.preview_rows.length > 0 && <>
      <p>アップロード内容（最大50行・重複を含む）</p>
      <div className="journal-import-table" tabIndex={0} role="region" aria-label="過去仕訳CSVプレビュー">
        <table><thead><tr>{columns.map(column => <th key={column}>{column}</th>)}</tr></thead>
          <tbody>{result.preview_rows.map((row, index) => <tr key={index}>
            {columns.map(column => <td key={column}>{row[column]}</td>)}
          </tr>)}</tbody></table>
      </div>
    </>}
    <button type="button" disabled={busy || result.new_count === 0 || result.errors.length > 0} onClick={onExecute}>
      検索DBへ追加
    </button>
  </>;
}

export default function JournalImport({ children }: { children?: ReactNode } = {}) {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<JournalImportResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [executing, setExecuting] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const control = useRef<HTMLInputElement>(null);
  const inFlight = useRef(false);

  useEffect(() => {
    let active = true;
    setPreview(null);
    setError("");
    setLoading(Boolean(file));
    if (file) void importJournalCsv(file, "preview").then(result => {
      if (active) setPreview(result);
    }).catch(() => {
      if (active) setError("CSVのPreviewに失敗しました。ファイルと検索DBを確認してください。");
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [file]);

  async function execute() {
    if (!file || !preview?.new_count || preview.errors.length || loading || inFlight.current) return;
    inFlight.current = true;
    setExecuting(true);
    setError("");
    setMessage("");
    try {
      const result = await importJournalCsv(file, "execute");
      if (result.errors.length) {
        setPreview(result);
      } else {
        setMessage(`検索DBへ${result.imported_count}件追加しました（重複${result.duplicate_count}件）。次の検索から反映されます。`);
        setFile(null);
        setPreview(null);
        if (control.current) control.current.value = "";
      }
    } catch {
      setError("取込結果を確認できませんでした。検索DBを確認して再試行してください。");
    } finally {
      inFlight.current = false;
      setExecuting(false);
    }
  }

  return <details className="journal-import">
    <summary>データ管理</summary>
    <OutputSettings />
    <h3>EPSON仕訳帳CSV取込</h3>
    <p>初回設定または過去DB更新時に使用します。過去仕訳は検索DBの末尾へ追加します。</p>
    <label>エプソン仕訳CSVアップロード
      <input ref={control} type="file" accept=".csv" disabled={executing} onChange={event => {
        setPreview(null); setMessage(""); setFile(event.target.files?.[0] ?? null);
      }} />
    </label>
    {loading && <p role="status">Previewを読み込み中…</p>}
    {executing && <p role="status">検索DBへ追加中…</p>}
    {error && <p role="alert" className="error-message">{error}</p>}
    {message && <p role="status">{message}</p>}
    {preview && <ImportPreview result={preview} busy={loading || executing} onExecute={() => void execute()} />}
    {children}
  </details>;
}
