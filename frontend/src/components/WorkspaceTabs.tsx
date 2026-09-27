export type Workspace = "journal" | "receivable" | "schedule";

export default function WorkspaceTabs({ active, onChange, journalDisabled = false }: {
  active: Workspace;
  onChange: (workspace: Workspace) => void;
  journalDisabled?: boolean;
}) {
  return <nav className="workspace-tabs" aria-label="業務画面" role="tablist">
    <button type="button" role="tab" aria-selected={active === "journal"} disabled={journalDisabled}
      className={active === "journal" ? "active" : ""} onClick={() => onChange("journal")}>通常仕訳</button>
    <button type="button" role="tab" aria-selected={active === "receivable"}
      className={active === "receivable" ? "active" : ""} onClick={() => onChange("receivable")}>未収消込</button>
    <button type="button" role="tab" aria-selected={active === "schedule"} disabled={journalDisabled}
      className={active === "schedule" ? "active" : ""} onClick={() => onChange("schedule")}>スケジュール</button>
  </nav>;
}
