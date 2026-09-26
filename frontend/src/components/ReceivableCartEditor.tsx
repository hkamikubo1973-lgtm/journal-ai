import { useState } from "react";
import { editReceivableCartItem } from "../api/journal";
import type { JournalMastersResponse, ReceivableRegistrationHandoffItem } from "../types/journal";

export default function ReceivableCartEditor({ item, masters, onSaved }: {
  item: ReceivableRegistrationHandoffItem;
  masters: JournalMastersResponse | null;
  onSaved: (next: ReceivableRegistrationHandoffItem) => void;
}) {
  const [open, setOpen] = useState(false);
  const [edits, setEdits] = useState(() => ({
    debit_account_code: item.prepared_journal.debit_account_code,
    credit_account_code: item.prepared_journal.credit_account_code,
    debit_sub_code: item.prepared_journal.debit_sub_code,
    credit_sub_code: item.prepared_journal.credit_sub_code,
    debit_dept_code: item.prepared_journal.debit_dept_code,
    credit_dept_code: item.prepared_journal.credit_dept_code,
    amount: String(item.prepared_journal.amount),
    summary: item.prepared_journal.summary,
  }));
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const set = (field: keyof typeof edits, value: string) => setEdits(current => ({ ...current, [field]: value }));
  async function save() {
    if (!masters || pending) return;
    setPending(true); setError("");
    try {
      const result = await editReceivableCartItem(item, { ...edits, amount: Number(edits.amount) });
      onSaved(result); setOpen(false);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "更新できませんでした。");
    } finally { setPending(false); }
  }
  const account = (side: "debit" | "credit") => edits[`${side}_account_code`];
  return <div className="receivable-cart-editor">
    <button type="button" onClick={() => setOpen(value => !value)}>{open ? "編集を閉じる" : "編集"}</button>
    {open && <div className="receivable-cart-edit-fields">
      {(["debit", "credit"] as const).map(side => <div className="receivable-cart-edit-side" key={side}>
        <label>{side === "debit" ? "借方科目" : "貸方科目"}<select value={account(side)} onChange={event => {
          setEdits(current => ({ ...current, [`${side}_account_code`]: event.target.value, [`${side}_sub_code`]: "" }));
        }}><option value="">選択してください</option>{masters?.accounts.filter(row => row.selectable || row.code === account(side)).map(row =>
          <option key={row.code} value={row.code}>{row.code} {row.name}</option>)}</select></label>
        <label>{side === "debit" ? "借方補助" : "貸方補助"}<select value={edits[`${side}_sub_code`]} onChange={event => set(`${side}_sub_code`, event.target.value)}>
          <option value="">なし</option>{masters?.sub_account_relations.filter(row => row.account_code === account(side)).map(row =>
            <option key={`${row.account_code}-${row.sub_code}`} value={row.sub_code}>{row.sub_code} {row.sub_name}</option>)}</select></label>
        <label>{side === "debit" ? "借方部門" : "貸方部門"}<select value={edits[`${side}_dept_code`]} onChange={event => set(`${side}_dept_code`, event.target.value)}>
          <option value="">なし</option>{masters?.departments.map(row => <option key={row.code} value={row.code}>{row.code} {row.name}</option>)}</select></label>
      </div>)}
      <label>金額<input type="number" min="1" step="1" value={edits.amount} onChange={event => set("amount", event.target.value)} /></label>
      <label>摘要<input value={edits.summary} onChange={event => set("summary", event.target.value)} /></label>
      {error && <p role="alert" className="error-message">{error}</p>}
      <button type="button" disabled={pending || !masters || !edits.amount || Number(edits.amount) <= 0} onClick={() => void save()}>
        {pending ? "更新中…" : "更新保存"}</button>
    </div>}
  </div>;
}
