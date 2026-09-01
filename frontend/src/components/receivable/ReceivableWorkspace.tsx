import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  ReceivableApiError,
  executeReceivableSettlement,
  fetchReceivableDetail,
  fetchReceivableOptions,
  fetchReceivableSummary,
  previewReceivableSettlement,
} from "../../api/receivable";
import type { JournalMastersResponse } from "../../types/journal";
import type {
  ReceivableCustomerDetailResponse,
  ReceivableOptionsResponse,
  ReceivablePreviewMode,
  ReceivablePreviewPattern,
  ReceivablePreviewResponse,
  ReceivableRecommendedAccount,
  ReceivableSettlementExecuteRequest,
  ReceivableSummaryResponse,
} from "../../types/receivable";

type ReceivableWorkspaceProps = {
  masters: JournalMastersResponse | null;
  mastersLoading: boolean;
  mastersError: string | null;
  onExecutionLockChange?: (locked: boolean) => void;
};

type ExecutionCandidate = Omit<ReceivableSettlementExecuteRequest, "idempotency_key">;

type PendingExecuteOperation = {
  idempotencyKey: string;
  requestBody: Readonly<ReceivableSettlementExecuteRequest>;
};

type StoredPendingExecuteOperation = PendingExecuteOperation & {
  version: 1;
};

const pendingExecuteStorageKey = "journal-ai.receivable.pending-execute.v1";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === "string";
}

function parseStoredPendingOperation(value: unknown): PendingExecuteOperation | null {
  if (!isRecord(value) || value.version !== 1 || typeof value.idempotencyKey !== "string") return null;
  if (!isRecord(value.requestBody)) return null;
  const request = value.requestBody;
  const mode = request.mode;
  if (
    !value.idempotencyKey.trim()
    || request.idempotency_key !== value.idempotencyKey
    || typeof request.preview_revision !== "string"
    || !/^[0-9a-f]{64}$/.test(request.preview_revision)
    || typeof request.customer_name !== "string"
    || !request.customer_name.trim()
    || typeof request.settlement_date !== "string"
    || !/^\d{4}-\d{2}-\d{2}$/.test(request.settlement_date)
    || typeof request.payment_amount !== "number"
    || !Number.isInteger(request.payment_amount)
    || request.payment_amount <= 0
    || typeof request.receipt_account !== "string"
    || !request.receipt_account.trim()
    || (mode !== null && mode !== "partial" && mode !== "difference_account")
    || !isNullableString(request.difference_account)
    || !isNullableString(request.difference_summary)
  ) return null;

  const requestBody = Object.freeze({
    idempotency_key: value.idempotencyKey,
    preview_revision: request.preview_revision,
    customer_name: request.customer_name,
    settlement_date: request.settlement_date,
    payment_amount: request.payment_amount,
    receipt_account: request.receipt_account,
    mode,
    difference_account: request.difference_account,
    difference_summary: request.difference_summary,
  });
  return { idempotencyKey: value.idempotencyKey, requestBody };
}

function clearStoredPendingOperation(): void {
  try {
    window.sessionStorage.removeItem(pendingExecuteStorageKey);
  } catch {
    // Storageが利用できない環境でも画面を壊さない。
  }
}

function restorePendingOperation(): PendingExecuteOperation | null {
  try {
    const stored = window.sessionStorage.getItem(pendingExecuteStorageKey);
    if (!stored) return null;
    const operation = parseStoredPendingOperation(JSON.parse(stored) as unknown);
    if (operation) return operation;
    clearStoredPendingOperation();
  } catch {
    clearStoredPendingOperation();
  }
  return null;
}

function storePendingOperation(operation: PendingExecuteOperation): boolean {
  const stored: StoredPendingExecuteOperation = { version: 1, ...operation };
  try {
    window.sessionStorage.setItem(pendingExecuteStorageKey, JSON.stringify(stored));
    return true;
  } catch {
    return false;
  }
}

const patternLabels: Record<ReceivablePreviewPattern, string> = {
  exact_match: "完全一致",
  partial_settlement: "部分消込",
  shortage_difference: "不足差額処理",
  overpayment: "過入金",
};

const modeLabels: Record<ReceivablePreviewMode, string> = {
  partial: "部分消込",
  difference_account: "差額科目処理",
};

function todayText(): string {
  const now = new Date();
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 10);
}

function formatAmount(value: number): string {
  return `${new Intl.NumberFormat("ja-JP").format(value)}円`;
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

function requiresRefresh(error: unknown): boolean {
  return error instanceof ReceivableApiError
    && (error.status === 404 || error.status === 409);
}

function patternGuidance(preview: ReceivablePreviewResponse): string {
  switch (preview.pattern) {
    case "exact_match":
      return "入金額と消込対象額が一致しています。";
    case "partial_settlement":
      return "未収残高の一部を消し込みます。必要に応じて差額科目処理へ切り替えられます。";
    case "shortage_difference":
      return "不足額を選択した差額科目で処理するPreviewです。";
    case "overpayment":
      return preview.preview_complete
        ? "過入金を選択した差額科目で処理するPreviewです。"
        : "過入金のため差額科目を選択してください。";
  }
}

export default function ReceivableWorkspace({
  masters,
  mastersLoading,
  mastersError,
  onExecutionLockChange,
}: ReceivableWorkspaceProps) {
  const [summary, setSummary] = useState<ReceivableSummaryResponse | null>(null);
  const [options, setOptions] = useState<ReceivableOptionsResponse | null>(null);
  const [selectedCustomer, setSelectedCustomer] = useState<string | null>(null);
  const [detail, setDetail] = useState<ReceivableCustomerDetailResponse | null>(null);
  const [preview, setPreview] = useState<ReceivablePreviewResponse | null>(null);
  const [previewRevision, setPreviewRevision] = useState<string | null>(null);
  const [availableModes, setAvailableModes] = useState<ReceivablePreviewMode[]>([]);
  const [recommendations, setRecommendations] = useState<ReceivableRecommendedAccount[]>([]);
  const [differenceAccountRequired, setDifferenceAccountRequired] = useState(false);
  const [settlementDate, setSettlementDate] = useState(todayText);
  const [paymentAmount, setPaymentAmount] = useState("");
  const [receiptAccount, setReceiptAccount] = useState("");
  const [mode, setMode] = useState<ReceivablePreviewMode | null>(null);
  const [differenceAccount, setDifferenceAccount] = useState("");
  const [differenceSummary, setDifferenceSummary] = useState("");
  const [summaryLoading, setSummaryLoading] = useState(false);
  const [optionsLoading, setOptionsLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [summaryError, setSummaryError] = useState<string | null>(null);
  const [optionsError, setOptionsError] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [refreshSuggested, setRefreshSuggested] = useState(false);
  const [executionCandidate, setExecutionCandidate] = useState<ExecutionCandidate | null>(null);
  const [pendingOperation, setPendingOperation] = useState<PendingExecuteOperation | null>(restorePendingOperation);
  const [restoredPending, setRestoredPending] = useState(() => pendingOperation !== null);
  const [executeLoading, setExecuteLoading] = useState(false);
  const [executeError, setExecuteError] = useState<string | null>(null);
  const [executeSuccess, setExecuteSuccess] = useState<string | null>(null);
  const detailRequestId = useRef(0);
  const previewRequestId = useRef(0);
  const executeInFlight = useRef(false);

  useEffect(() => {
    void refreshWorkspace(false);
  }, []);

  useEffect(() => {
    onExecutionLockChange?.(executeLoading || pendingOperation !== null);
  }, [executeLoading, onExecutionLockChange, pendingOperation]);

  useEffect(() => () => onExecutionLockChange?.(false), [onExecutionLockChange]);

  useEffect(() => {
    if (!pendingOperation) return;
    const warnBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warnBeforeUnload);
    return () => window.removeEventListener("beforeunload", warnBeforeUnload);
  }, [pendingOperation]);

  function resetPreviewState(resetChoices = true): void {
    previewRequestId.current += 1;
    setPreview(null);
    setExecutionCandidate(null);
    setPreviewError(null);
    setPreviewLoading(false);
    if (resetChoices) {
      setMode(null);
      setDifferenceAccount("");
      setDifferenceSummary("");
      setAvailableModes([]);
      setRecommendations([]);
      setDifferenceAccountRequired(false);
    }
  }

  function applyOptions(response: ReceivableOptionsResponse): void {
    setOptions(response);
    setReceiptAccount((current) => {
      if (response.receipt_accounts.some((item) => item.name === current)) {
        return current;
      }
      const fallback = response.default_receipt_account;
      return fallback
        && response.receipt_accounts.some((item) => item.name === fallback)
        ? fallback
        : "";
    });
  }

  async function loadDetail(customerName: string, missingIsNormal = false): Promise<void> {
    const requestId = ++detailRequestId.current;
    setDetailLoading(true);
    setDetail(null);
    setDetailError(null);
    setPreviewRevision(null);
    try {
      const response = await fetchReceivableDetail(customerName);
      if (requestId !== detailRequestId.current) return;
      setDetail(response);
      setPreviewRevision(response.ledger_revision);
    } catch (error) {
      if (requestId !== detailRequestId.current) return;
      if (missingIsNormal && error instanceof ReceivableApiError && error.status === 404) {
        setSelectedCustomer(null);
        setDetail(null);
        setDetailError(null);
        setPreviewRevision(null);
        return;
      }
      setDetailError(errorMessage(error, "未収明細を取得できませんでした。"));
      setRefreshSuggested(requiresRefresh(error));
    } finally {
      if (requestId === detailRequestId.current) setDetailLoading(false);
    }
  }

  async function refreshLedgerAfterOperation(customerName: string): Promise<void> {
    detailRequestId.current += 1;
    setSummaryLoading(true);
    setDetailLoading(true);
    setSummaryError(null);
    setDetailError(null);
    setRefreshSuggested(false);
    try {
      const nextSummary = await fetchReceivableSummary();
      setSummary(nextSummary);
      if (!nextSummary.customers.some((item) => item.customer_name === customerName)) {
        setSelectedCustomer(null);
        setDetail(null);
        setPreviewRevision(null);
        setDetailLoading(false);
        return;
      }
      await loadDetail(customerName, true);
    } catch (error) {
      setSummaryError(errorMessage(error, "未収集計を再取得できませんでした。"));
      setRefreshSuggested(true);
      setDetailLoading(false);
    } finally {
      setSummaryLoading(false);
    }
  }

  async function refreshWorkspace(refreshSelected = true): Promise<void> {
    const customerToRefresh = refreshSelected ? selectedCustomer : null;
    detailRequestId.current += 1;
    resetPreviewState();
    setExecuteError(null);
    setExecuteSuccess(null);
    setRefreshSuggested(false);
    setSummaryLoading(true);
    setOptionsLoading(true);
    setSummaryError(null);
    setOptionsError(null);
    if (customerToRefresh) {
      setDetail(null);
      setDetailError(null);
      setDetailLoading(true);
      setPreviewRevision(null);
    }

    const [summaryResult, optionsResult] = await Promise.allSettled([
      fetchReceivableSummary(),
      fetchReceivableOptions(),
    ]);

    let nextSummary: ReceivableSummaryResponse | null = null;
    if (summaryResult.status === "fulfilled") {
      nextSummary = summaryResult.value;
      setSummary(nextSummary);
    } else {
      setSummary(null);
      setSelectedCustomer(null);
      setDetail(null);
      setDetailLoading(false);
      setDetailError(null);
      setPreviewRevision(null);
      resetPreviewState();
      setSummaryError(errorMessage(summaryResult.reason, "未収集計を取得できませんでした。"));
    }
    setSummaryLoading(false);

    if (optionsResult.status === "fulfilled") {
      applyOptions(optionsResult.value);
    } else {
      setOptions(null);
      setReceiptAccount("");
      setOptionsError(errorMessage(optionsResult.reason, "入金科目を取得できませんでした。"));
    }
    setOptionsLoading(false);

    if (!customerToRefresh) return;
    if (!nextSummary?.customers.some((item) => item.customer_name === customerToRefresh)) {
      setSelectedCustomer(null);
      setDetail(null);
      setDetailLoading(false);
      setDetailError("選択していた得意先が一覧にありません。得意先を選び直してください。");
      return;
    }
    await loadDetail(customerToRefresh);
  }

  function selectCustomer(customerName: string): void {
    if (executeLoading || pendingOperation) return;
    if (customerName === selectedCustomer && detail) return;
    setSelectedCustomer(customerName);
    setDetailError(null);
    setRefreshSuggested(false);
    setPaymentAmount("");
    resetPreviewState();
    setExecuteError(null);
    setExecuteSuccess(null);
    void loadDetail(customerName);
  }

  function changeCoreInput(change: () => void): void {
    if (executeLoading || pendingOperation) return;
    change();
    resetPreviewState();
    setExecuteError(null);
    setExecuteSuccess(null);
  }

  const parsedPaymentAmount = Number(paymentAmount);
  const validPaymentAmount = paymentAmount !== ""
    && Number.isInteger(parsedPaymentAmount)
    && parsedPaymentAmount > 0;
  const settlementAvailable = Boolean(
    summary?.settlement_available && detail?.settlement_available,
  );
  const hasReceiptOptions = Boolean(options?.receipt_accounts.length);
  const canPreview = Boolean(
    selectedCustomer
    && detail
    && previewRevision
    && settlementAvailable
    && settlementDate
    && validPaymentAmount
    && receiptAccount
    && !previewLoading
    && !executeLoading
    && !pendingOperation,
  );

  async function runPreview(
    requestedMode: ReceivablePreviewMode | null = mode,
    requestedDifferenceAccount = differenceAccount,
    requestedDifferenceSummary = differenceSummary,
  ): Promise<void> {
    if (!canPreview || !selectedCustomer || !previewRevision) return;
    const requestId = ++previewRequestId.current;
    setPreview(null);
    setPreviewError(null);
    setExecuteError(null);
    setExecuteSuccess(null);
    setPreviewLoading(true);
    try {
      const response = await previewReceivableSettlement({
        ledger_revision: previewRevision,
        customer_name: selectedCustomer,
        settlement_date: settlementDate,
        payment_amount: parsedPaymentAmount,
        receipt_account: receiptAccount,
        mode: requestedMode,
        difference_account: requestedDifferenceAccount || null,
        difference_summary: requestedDifferenceSummary || null,
      });
      if (requestId !== previewRequestId.current) return;
      setPreview(response);
      setPreviewRevision(response.ledger_revision);
      setMode(response.mode);
      setAvailableModes(response.available_modes);
      setRecommendations(response.recommended_difference_accounts);
      setDifferenceAccountRequired(response.difference_account_required);
      setExecutionCandidate(response.preview_complete && response.rows.length > 0
        ? Object.freeze({
          preview_revision: response.ledger_revision,
          customer_name: response.customer_name,
          settlement_date: response.settlement_date,
          payment_amount: response.payment_amount,
          receipt_account: response.receipt_account,
          mode: response.mode,
          difference_account: requestedDifferenceAccount || null,
          difference_summary: requestedDifferenceSummary || null,
        })
        : null);
    } catch (error) {
      if (requestId !== previewRequestId.current) return;
      setPreviewError(errorMessage(error, "未収Previewを取得できませんでした。"));
      setRefreshSuggested(requiresRefresh(error));
    } finally {
      if (requestId === previewRequestId.current) setPreviewLoading(false);
    }
  }

  function submitPreview(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    void runPreview();
  }

  function changeMode(nextMode: ReceivablePreviewMode): void {
    if (executeLoading || pendingOperation) return;
    previewRequestId.current += 1;
    setPreview(null);
    setExecutionCandidate(null);
    setPreviewError(null);
    setExecuteError(null);
    setExecuteSuccess(null);
    setMode(nextMode);
    if (nextMode === "partial") {
      setDifferenceAccount("");
      setDifferenceSummary("");
      void runPreview(nextMode, "", "");
    }
  }

  function chooseDifferenceAccount(accountName: string): void {
    if (executeLoading || pendingOperation) return;
    previewRequestId.current += 1;
    setPreview(null);
    setExecutionCandidate(null);
    setPreviewError(null);
    setExecuteError(null);
    setExecuteSuccess(null);
    setDifferenceAccount(accountName);
    setMode("difference_account");
    if (accountName) void runPreview("difference_account", accountName);
  }

  async function runExecute(operation: PendingExecuteOperation): Promise<void> {
    if (executeInFlight.current) return;
    executeInFlight.current = true;
    setPendingOperation(operation);
    setExecuteLoading(true);
    setExecuteError(null);
    setExecuteSuccess(null);
    try {
      const response = await executeReceivableSettlement(operation.requestBody);
      clearStoredPendingOperation();
      setPendingOperation(null);
      setRestoredPending(false);
      resetPreviewState();
      setExecuteSuccess(response.message || "未収消込が完了しました");
      await refreshLedgerAfterOperation(operation.requestBody.customer_name);
    } catch (error) {
      if (!(error instanceof ReceivableApiError)) {
        setExecuteError("処理結果を確認できませんでした。同じ操作IDで再確認できます。");
        return;
      }
      if (error.status === 423) {
        setExecuteError(`${error.message} 同じ操作IDで再確認できます。`);
        return;
      }
      if (error.status >= 500) {
        setExecuteError(`${error.message} 処理結果が不明なため、同じ操作IDで再確認してください。`);
        return;
      }

      clearStoredPendingOperation();
      setPendingOperation(null);
      setRestoredPending(false);
      if (error.status === 409) {
        resetPreviewState();
        if (error.message.includes("操作ID")) {
          setExecuteError("操作IDの整合性を確認できません。再読込後にもう一度Previewしてください。");
          setRefreshSuggested(true);
          return;
        }
        setExecuteError(`${error.message} 最新データで再度Previewしてください。`);
        await refreshLedgerAfterOperation(operation.requestBody.customer_name);
        return;
      }
      if (error.status === 422) {
        setExecutionCandidate(null);
        setExecuteError(`${error.message} 入力を修正し、もう一度Previewしてください。`);
        return;
      }
      setExecutionCandidate(null);
      setExecuteError(error.message);
    } finally {
      executeInFlight.current = false;
      setExecuteLoading(false);
    }
  }

  function startExecute(): void {
    if (!executionCandidate || pendingOperation || executeInFlight.current) return;
    let idempotencyKey: string;
    try {
      idempotencyKey = crypto.randomUUID();
    } catch {
      setExecuteError("安全な操作IDを生成できませんでした。ブラウザ環境を確認してください。");
      return;
    }
    const requestBody = Object.freeze({
      idempotency_key: idempotencyKey,
      ...executionCandidate,
    });
    const operation = { idempotencyKey, requestBody };
    setPendingOperation(operation);
    setRestoredPending(false);
    if (!storePendingOperation(operation)) {
      setPendingOperation(null);
      setExecuteError("再確認に必要な操作情報を一時保存できないため、消込を開始しませんでした。");
      return;
    }
    void runExecute(operation);
  }

  function retryExecute(): void {
    if (!pendingOperation || executeInFlight.current) return;
    if (!storePendingOperation(pendingOperation)) {
      setExecuteError("再確認に必要な操作情報を一時保存できないため、送信しませんでした。");
      return;
    }
    setRestoredPending(false);
    void runExecute(pendingOperation);
  }

  const recommendationCodes = new Set(recommendations.map((item) => item.code));
  const showDifferenceAccount = mode === "difference_account"
    || differenceAccountRequired;
  const canExecute = Boolean(
    preview?.preview_complete
    && preview.rows.length > 0
    && executionCandidate
    && settlementAvailable
    && !executeLoading
    && !pendingOperation,
  );
  const executionLocked = executeLoading || pendingOperation !== null;

  return (
    <section className="receivable-workspace" aria-label="未収消込">
      <aside className="receivable-panel receivable-summary-panel">
        <div className="receivable-panel-heading">
          <div><p className="eyebrow">Receivables</p><h2>未収一覧</h2></div>
          <button type="button" className="receivable-refresh" onClick={() => void refreshWorkspace()}
            disabled={summaryLoading || optionsLoading || detailLoading || previewLoading || executionLocked}>
            再読込
          </button>
        </div>
        {summaryLoading && <p className="receivable-loading" role="status">未収集計を読み込み中…</p>}
        {summaryError && <p className="error-message" role="alert">{summaryError}</p>}
        {summary && <>
          <div className="receivable-metrics">
            <div><span>未収総残高</span><strong>{formatAmount(summary.outstanding_balance)}</strong></div>
            <div><span>未収件数</span><strong>{summary.outstanding_count}件</strong></div>
            <div><span>得意先数</span><strong>{summary.customer_count}社</strong></div>
          </div>
          {!summary.settlement_available && <p className="notice notice-warning">
            未収台帳の状態を確認する必要があります。一覧は参照できますが、Preview操作はできません。
          </p>}
          <div className="receivable-customer-list" aria-label="得意先一覧">
            {summary.customers.map((customer) => <button
              type="button"
              className={`receivable-customer${selectedCustomer === customer.customer_name ? " selected" : ""}`}
              key={customer.customer_name}
              onClick={() => selectCustomer(customer.customer_name)}
              disabled={executionLocked}
            >
              <span>{customer.customer_name}</span>
              <small>{customer.outstanding_count}件</small>
              <strong>{formatAmount(customer.outstanding_balance)}</strong>
            </button>)}
            {summary.customers.length === 0 && <p className="muted">未収残高のある得意先はありません。</p>}
          </div>
        </>}
        {summary && <p className="receivable-revision" title={summary.ledger_revision}>
          台帳revision: {summary.ledger_revision.slice(0, 12)}…
        </p>}
      </aside>

      <section className="receivable-panel receivable-detail-panel" aria-live="polite">
        <div className="receivable-panel-heading">
          <div><p className="eyebrow">Customer detail</p><h2>未収明細</h2></div>
          {detail && <span className="result-pill">{detail.outstanding_count}件 / {formatAmount(detail.outstanding_balance)}</span>}
        </div>
        {!selectedCustomer && <div className="receivable-empty"><strong>得意先を選択してください</strong><p>左の一覧から未収明細を確認できます。</p></div>}
        {detailLoading && <p className="receivable-loading" role="status">未収明細を読み込み中…</p>}
        {detailError && <p className="error-message" role="alert">{detailError}</p>}
        {detail && <>
          <div className="receivable-detail-title">
            <strong>{detail.customer_name}</strong>
            <span>{detail.settlement_available ? "Preview可能" : "参照のみ"}</span>
          </div>
          {!detail.settlement_available && <p className="notice notice-warning">未収台帳の状態を確認する必要があります。</p>}
          <div className="receivable-table-scroll">
            <table>
              <thead><tr>
                <th>請求日</th><th>入金予定日</th><th>未収科目</th><th>補助</th><th>部門</th><th>摘要</th>
                <th>請求額</th><th>入金済額</th><th>残高</th><th>状態</th>
              </tr></thead>
              <tbody>{detail.receivables.map((item) => <tr key={`${item.code}-${item.receivable_id}`}>
                <td>{item.billing_date || "-"}</td><td>{item.planned_payment_date || "-"}</td>
                <td>{item.receivable_account || "-"}</td><td>{item.receivable_sub_account || "-"}</td>
                <td>{item.department || "-"}</td><td title={item.summary}>{item.summary || "-"}</td>
                <td className="table-amount">{formatAmount(item.billed_amount)}</td>
                <td className="table-amount">{formatAmount(item.paid_amount)}</td>
                <td className="table-amount">{formatAmount(item.balance)}</td><td>{item.status || "-"}</td>
              </tr>)}</tbody>
            </table>
          </div>
          <p className="receivable-revision" title={detail.ledger_revision}>
            Preview基準revision: {detail.ledger_revision.slice(0, 12)}…
          </p>
        </>}
      </section>

      <section className="receivable-panel receivable-preview-panel" aria-live="polite">
        <div className="receivable-panel-heading">
          <div><p className="eyebrow">Preview & Execute</p><h2>入金・仕訳Preview</h2></div>
          <span className="status-badge">{pendingOperation
            ? "結果未確認"
            : preview?.preview_complete ? "確定可能" : "確認中"}</span>
        </div>
        <form className="receivable-preview-form" onSubmit={submitPreview}>
          <label>入金日<input type="date" value={settlementDate}
            onChange={(event) => changeCoreInput(() => setSettlementDate(event.target.value))}
            disabled={!detail || !settlementAvailable || executionLocked} /></label>
          <label>入金額<input type="number" min="1" step="1" inputMode="numeric" value={paymentAmount}
            onChange={(event) => changeCoreInput(() => setPaymentAmount(event.target.value))}
            disabled={!detail || !settlementAvailable || executionLocked} placeholder="0" /></label>
          <label>入金科目<select value={receiptAccount}
            onChange={(event) => changeCoreInput(() => setReceiptAccount(event.target.value))}
            disabled={!detail || !settlementAvailable || optionsLoading || !hasReceiptOptions || executionLocked}>
            <option value="">{optionsLoading ? "読み込み中…" : "入金科目を選択"}</option>
            {options?.receipt_accounts.map((account) => <option value={account.name} key={account.code}>
              {account.name}（{account.code}）
            </option>)}
          </select></label>
          <button type="submit" disabled={!canPreview}>{previewLoading ? "Preview中…" : "Preview"}</button>
        </form>
        {optionsError && <p className="error-message" role="alert">{optionsError}</p>}
        {!optionsLoading && options?.receipt_accounts.length === 0 && <p className="notice notice-warning">
          利用できる入金科目がありません。Previewは実行できません。
        </p>}
        {executeSuccess && <p className="notice notice-success" role="status">{executeSuccess}</p>}
        {executeError && <p className="error-message" role="alert">{executeError}</p>}
        {pendingOperation && !executeLoading && <div className="receivable-retry-panel">
          <div>
            <strong>{restoredPending
              ? "前回の消込処理の結果を確認できていません。"
              : "消込処理の結果を確認できていません。"}</strong>
            <p>送信時に固定した内容を変更せず、同じ操作IDで結果を再確認します。</p>
            <dl>
              <div><dt>得意先</dt><dd>{pendingOperation.requestBody.customer_name}</dd></div>
              <div><dt>入金日</dt><dd>{pendingOperation.requestBody.settlement_date}</dd></div>
              <div><dt>入金額</dt><dd>{formatAmount(pendingOperation.requestBody.payment_amount)}</dd></div>
              <div><dt>入金科目</dt><dd>{pendingOperation.requestBody.receipt_account}</dd></div>
            </dl>
          </div>
          <button type="button" onClick={retryExecute}>同じ内容で再確認</button>
        </div>}
        {executeLoading && <p className="receivable-loading" role="status">未収消込を確認中…</p>}
        {refreshSuggested && <button type="button" className="receivable-reload-guidance" onClick={() => void refreshWorkspace()}
          disabled={executionLocked}>
          未収一覧と明細を再読込
        </button>}
        {previewError && <p className="error-message" role="alert">{previewError}</p>}
        {previewLoading && <p className="receivable-loading" role="status">Previewを取得中…</p>}

        {availableModes.length > 0 && <div className="receivable-mode-panel">
          <span>処理方法</span>
          <div>{availableModes.map((availableMode) => <button type="button"
            className={mode === availableMode ? "selected" : ""}
            key={availableMode} onClick={() => changeMode(availableMode)} disabled={previewLoading || executionLocked}>
            {modeLabels[availableMode]}
          </button>)}</div>
        </div>}

        {showDifferenceAccount && <div className="receivable-difference-panel">
          <label>差額科目<select value={differenceAccount} onChange={(event) => chooseDifferenceAccount(event.target.value)}
            disabled={previewLoading || mastersLoading || !masters || Boolean(mastersError) || executionLocked}>
            <option value="">差額科目を選択してください</option>
            {recommendations.length > 0 && <optgroup label="推奨候補">
              {recommendations.map((account) => <option value={account.name} key={`recommended-${account.code}`}>
                {account.name}（{account.code}）
              </option>)}
            </optgroup>}
            <optgroup label="全科目">
              {masters?.accounts.filter((account) => !recommendationCodes.has(account.code)).map((account) => <option
                value={account.name} key={`master-${account.code}`} disabled={!account.selectable}>
                {account.name}（{account.code}）{account.selectable ? "" : "［選択不可］"}
              </option>)}
            </optgroup>
          </select></label>
          <label>差額摘要（任意）<input value={differenceSummary}
            onChange={(event) => {
              if (executionLocked) return;
              setDifferenceSummary(event.target.value);
              resetPreviewState(false);
              setExecuteError(null);
              setExecuteSuccess(null);
            }}
            disabled={previewLoading || executionLocked} placeholder="空欄時はBackend既定値" /></label>
          {recommendations.length > 0 && <div className="receivable-recommendations">
            <span>推奨（Backend順位）</span>
            {recommendations.map((account) => <button type="button" key={account.code}
              onClick={() => chooseDifferenceAccount(account.name)} disabled={previewLoading || executionLocked}>
              {account.name}<small>{account.code}</small>
            </button>)}
          </div>}
          {mastersError && <p className="error-message">全科目を読み込めませんでした。</p>}
        </div>}

        {preview && <div className={`receivable-preview-result${preview.preview_complete ? " complete" : " incomplete"}`}>
          <div className="receivable-pattern-heading">
            <div><span>判定</span><strong>{patternLabels[preview.pattern]}</strong></div>
            <p>{patternGuidance(preview)}</p>
          </div>
          <div className="receivable-preview-metrics">
            <div><span>未収残高合計</span><strong>{formatAmount(preview.total_receivable_balance)}</strong></div>
            <div><span>入金額</span><strong>{formatAmount(preview.payment_amount)}</strong></div>
            <div><span>元差額</span><strong>{formatAmount(preview.original_difference)}</strong></div>
            <div><span>消込対象額</span><strong>{formatAmount(preview.target_total)}</strong></div>
            <div><span>最終差額</span><strong>{formatAmount(preview.difference)}</strong></div>
          </div>
          <div className="receivable-execute-summary">
            <span><small>入金日</small>{preview.settlement_date}</span>
            <span><small>得意先</small>{preview.customer_name}</span>
            <span><small>入金科目</small>{preview.receipt_account}</span>
          </div>

          <section className="receivable-result-section">
            <div className="section-heading-row"><h3>今回の消込対象</h3><span>FIFO・Backend順</span></div>
            <div className="receivable-result-scroll"><table>
              <thead><tr><th>請求日</th><th>未収ID</th><th>未収科目</th><th>補助</th><th>摘要</th><th>残高</th><th>消込予定額</th></tr></thead>
              <tbody>{preview.source_candidates.map((candidate) => <tr key={`${candidate.code}-${candidate.receivable_id}`}>
                <td>{candidate.billing_date}</td><td>{candidate.receivable_id}</td><td>{candidate.receivable_account}</td>
                <td>{candidate.receivable_sub_account || "-"}</td><td title={candidate.summary}>{candidate.summary || "-"}</td>
                <td className="table-amount">{formatAmount(candidate.balance)}</td>
                <td className="table-amount">{formatAmount(candidate.scheduled_amount)}</td>
              </tr>)}</tbody>
            </table></div>
          </section>

          {preview.preview_complete ? <section className="receivable-result-section">
            <div className="section-heading-row"><h3>仕訳Preview</h3><span>確認のみ・未確定</span></div>
            <div className="receivable-result-scroll"><table>
              <thead><tr><th>借方科目</th><th>貸方科目</th><th>貸方補助</th><th>部門</th><th>金額</th><th>摘要</th></tr></thead>
              <tbody>{preview.rows.map((row, index) => <tr key={`${index}-${row.debit_account}-${row.credit_account}`}>
                <td>{row.debit_account || "-"}</td><td>{row.credit_account || "-"}</td>
                <td>{row.credit_sub_account || "-"}</td><td>{row.department || "-"}</td>
                <td className="table-amount">{formatAmount(row.amount)}</td><td title={row.summary}>{row.summary || "-"}</td>
              </tr>)}</tbody>
            </table></div>
            <div className="receivable-execute-actions">
              <p>表示中の消込対象と仕訳PreviewをBackendで再検証して確定します。</p>
              <button type="button" onClick={startExecute} disabled={!canExecute}>
                {executeLoading ? "消込確定中…" : "この内容で消込確定"}
              </button>
            </div>
          </section> : <p className="notice notice-warning">必要な差額科目を選択すると、仕訳Previewを確認できます。</p>}
          <p className="receivable-revision" title={preview.ledger_revision}>
            Preview revision: {preview.ledger_revision.slice(0, 12)}…
          </p>
        </div>}
      </section>
    </section>
  );
}
