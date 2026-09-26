import type { ReceivableCleanupResponse, ReceivableSummaryResponse } from "../../types/receivable";

export function selectedCustomerAfterRefresh(
  selected: string | null,
  summary: ReceivableSummaryResponse | null,
): string | null {
  return selected && summary?.customers.some((item) => item.customer_name === selected)
    ? selected : null;
}

type Props = {
  summary: ReceivableCleanupResponse | null;
  loading: boolean;
  executing: boolean;
  disabled: boolean;
  error: string | null;
  success: string | null;
  onExecute: () => void;
};

export default function ReceivableCleanup({ summary, loading, executing, disabled, error, success, onExecute }: Props) {
  return <details className="receivable-panel receivable-cleanup">
    <summary>未収台帳の整理</summary>
    {loading && <p role="status">整理対象を確認中…</p>}
    {summary && <p>完了済みまたは残高0の未収が {summary.cleanup_target_count}件あります</p>}
    <p>整理すると、current.csvから完了済み未収を除外します。消込履歴と検索DBは削除されません。</p>
    {error && <p className="error-message" role="alert">{error}</p>}
    {success && <p className="notice notice-success" role="status">{success}</p>}
    <button type="button" onClick={onExecute}
      disabled={!summary || summary.cleanup_target_count === 0 || loading || executing || disabled}>
      {executing ? "整理中…" : "完了済み未収を整理する"}
    </button>
  </details>;
}
