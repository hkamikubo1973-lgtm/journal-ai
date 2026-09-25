import { useRef, useState, type FormEvent } from "react";
import { addAccount, updateMasters, type MasterKind, type MasterUpdatePreview } from "../api/masterUpdate";

const labels: Record<MasterKind, string> = { account: "科目", department: "部門", sub: "補助" };

export function MasterUpdateButtons({ pendingMasterTarget, busy, onSelect }: {
  pendingMasterTarget: MasterKind | null; busy: boolean; onSelect: (kind: MasterKind) => void;
}) {
  return <div className="master-update-actions">{(Object.keys(labels) as MasterKind[]).map(kind => {
    const pending = pendingMasterTarget === kind;
    return <button key={kind} type="button" disabled={pending} aria-disabled={busy}
      aria-busy={pending} className={pending ? "master-update-pending" : undefined}
      onClick={() => { if (!busy) onSelect(kind); }}>
      {labels[kind]}マスター作成・更新{pending ? "（処理中…）" : ""}
    </button>;
  })}</div>;
}

export function MasterDiff({ result, busy, onExecute }: {
  result: MasterUpdatePreview; busy: boolean; onExecute: () => void;
}) {
  return <div>
    <h4>{labels[result.kind]}マスター差分</h4>
    <dl className="journal-import-summary">
      <div><dt>現在</dt><dd>{result.current_count}</dd></div>
      <div><dt>抽出</dt><dd>{result.found_count}</dd></div>
      <div><dt>追加</dt><dd>{result.added_count}</dd></div>
      <div><dt>登録済み</dt><dd>{result.unchanged_count}</dd></div>
      <div><dt>競合</dt><dd>{result.conflict_count}</dd></div>
      {result.kind === "sub" && <><div><dt>補助追加</dt><dd>{result.sub_added_count}</dd></div>
        <div><dt>親子関係追加</dt><dd>{result.relation_added_count}</dd></div></>}
    </dl>
    <ul className="master-change-list">{result.added_items.map((item, index) => <li key={index}>{item.code} {item.name}</li>)}</ul>
    {result.relation_added_items && <ul className="master-change-list">{result.relation_added_items.map((item, index) =>
      <li key={index}>親科目 {item.account_code} / 補助 {item.sub_code} {item.sub_name}</li>)}</ul>}
    {result.conflicts.map((item, index) => <p role="alert" key={index}>
      {item.code ?? item.account_code} {item.sub_code} {item.name ?? item.sub_name}：{item.reason}
    </p>)}
    <button type="button" disabled={busy || result.applied || result.added_count === 0 || result.conflict_count > 0} onClick={onExecute}>確定更新</button>
  </div>;
}

export default function MasterManagement({ onUpdated }: { onUpdated: () => Promise<void> }) {
  const [preview, setPreview] = useState<MasterUpdatePreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [pendingMasterTarget, setPendingMasterTarget] = useState<MasterKind | null>(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [category, setCategory] = useState("資産");
  const [payment, setPayment] = useState(false);
  const inFlight = useRef(false);

  async function run(action: () => Promise<void>, target: MasterKind | null = null) {
    if (inFlight.current) return;
    inFlight.current = true;
    setPendingMasterTarget(target);
    setBusy(true); setError(""); setMessage("");
    try { await action(); } catch (caught) {
      setError(caught instanceof Error ? caught.message : "処理に失敗しました。");
    } finally { inFlight.current = false; setBusy(false); setPendingMasterTarget(null); }
  }
  function loadPreview(kind: MasterKind) {
    void run(async () => { setPreview(null); setPreview(await updateMasters(kind)); }, kind);
  }
  function execute() {
    if (!preview || preview.conflict_count || !preview.added_count || preview.applied) return;
    void run(async () => {
      const result = await updateMasters(preview.kind, true);
      setPreview(result);
      if (result.applied) {
        setMessage(`${labels[result.kind]}マスターを更新しました（追加${result.added_count}件）。`);
        await onUpdated();
      }
    }, preview.kind);
  }
  function add(event: FormEvent) {
    event.preventDefault();
    void run(async () => {
      const result = await addAccount({ code, name, category, add_to_payment: payment });
      setPreview(null);
      setMessage(result.message + (result.payment_added ? " 入金科目候補にも追加しました。" : ""));
      await onUpdated();
    });
  }
  return <div className="master-management">
    <details><summary>マスター作成・更新</summary>
      <p>既存行を保持し、検索DBにある未登録項目を追加します。</p>
      <MasterUpdateButtons pendingMasterTarget={pendingMasterTarget} busy={busy} onSelect={loadPreview} />
      {preview && <MasterDiff result={preview} busy={busy} onExecute={execute} />}
    </details>
    <details><summary>科目マスター管理</summary>
      <h4>科目候補の追加</h4>
      <p>※ EPSON側に登録済みの科目だけを追加してください。</p>
      <p>※ journal-aiで追加してもEPSON側には登録されません。</p>
      <p>※ 分類は検索・確認・AIチェック用の補助情報です。</p>
      <form onSubmit={add}>
        <label>科目コード<input required value={code} disabled={busy} onChange={e => setCode(e.target.value)} /></label>
        <label>科目名<input required value={name} disabled={busy} onChange={e => setName(e.target.value)} /></label>
        <label>分類<select value={category} disabled={busy} onChange={e => setCategory(e.target.value)}>
          {["資産", "負債", "純資産", "収益", "費用"].map(value => <option key={value}>{value}</option>)}</select></label>
        <label className="master-payment-toggle"><input type="checkbox" checked={payment} disabled={busy} onChange={e => setPayment(e.target.checked)} />入金科目候補にも追加する</label>
        <button type="submit" disabled={busy || !code.trim() || !name.trim()}>追加する</button>
      </form>
    </details>
    {busy && <p role="status">{pendingMasterTarget ? `${labels[pendingMasterTarget]}マスターを処理中…` : "科目候補を追加中…"}</p>}
    {message && <p role="status">{message}</p>}
    {error && <p role="alert" className="error-message">{error}</p>}
  </div>;
}
