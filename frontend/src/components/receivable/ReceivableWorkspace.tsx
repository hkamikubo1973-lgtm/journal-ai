import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  ReceivableApiError,
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
  ReceivableSummaryResponse,
} from "../../types/receivable";

type ReceivableWorkspaceProps = {
  masters: JournalMastersResponse | null;
  mastersLoading: boolean;
  mastersError: string | null;
};

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
  const detailRequestId = useRef(0);
  const previewRequestId = useRef(0);

  useEffect(() => {
    void refreshWorkspace(false);
  }, []);

  function resetPreviewState(resetChoices = true): void {
    previewRequestId.current += 1;
    setPreview(null);
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

  async function loadDetail(customerName: string): Promise<void> {
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
      setDetailError(errorMessage(error, "未収明細を取得できませんでした。"));
      setRefreshSuggested(requiresRefresh(error));
    } finally {
      if (requestId === detailRequestId.current) setDetailLoading(false);
    }
  }

  async function refreshWorkspace(refreshSelected = true): Promise<void> {
    const customerToRefresh = refreshSelected ? selectedCustomer : null;
    detailRequestId.current += 1;
    resetPreviewState();
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
    if (customerName === selectedCustomer && detail) return;
    setSelectedCustomer(customerName);
    setDetailError(null);
    setRefreshSuggested(false);
    setPaymentAmount("");
    resetPreviewState();
    void loadDetail(customerName);
  }

  function changeCoreInput(change: () => void): void {
    change();
    resetPreviewState();
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
    && !previewLoading,
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
    previewRequestId.current += 1;
    setPreview(null);
    setPreviewError(null);
    setMode(nextMode);
    if (nextMode === "partial") {
      setDifferenceAccount("");
      setDifferenceSummary("");
      void runPreview(nextMode, "", "");
    }
  }

  function chooseDifferenceAccount(accountName: string): void {
    previewRequestId.current += 1;
    setPreview(null);
    setPreviewError(null);
    setDifferenceAccount(accountName);
    setMode("difference_account");
    if (accountName) void runPreview("difference_account", accountName);
  }

  const recommendationCodes = new Set(recommendations.map((item) => item.code));
  const showDifferenceAccount = mode === "difference_account"
    || differenceAccountRequired;

  return (
    <section className="receivable-workspace" aria-label="未収消込">
      <aside className="receivable-panel receivable-summary-panel">
        <div className="receivable-panel-heading">
          <div><p className="eyebrow">Receivables</p><h2>未収一覧</h2></div>
          <button type="button" className="receivable-refresh" onClick={() => void refreshWorkspace()}
            disabled={summaryLoading || optionsLoading || detailLoading || previewLoading}>
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
          <div><p className="eyebrow">Read-only Preview</p><h2>入金・仕訳Preview</h2></div>
          <span className="status-badge">確認のみ</span>
        </div>
        <form className="receivable-preview-form" onSubmit={submitPreview}>
          <label>入金日<input type="date" value={settlementDate}
            onChange={(event) => changeCoreInput(() => setSettlementDate(event.target.value))}
            disabled={!detail || !settlementAvailable} /></label>
          <label>入金額<input type="number" min="1" step="1" inputMode="numeric" value={paymentAmount}
            onChange={(event) => changeCoreInput(() => setPaymentAmount(event.target.value))}
            disabled={!detail || !settlementAvailable} placeholder="0" /></label>
          <label>入金科目<select value={receiptAccount}
            onChange={(event) => changeCoreInput(() => setReceiptAccount(event.target.value))}
            disabled={!detail || !settlementAvailable || optionsLoading || !hasReceiptOptions}>
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
        {refreshSuggested && <button type="button" className="receivable-reload-guidance" onClick={() => void refreshWorkspace()}>
          未収一覧と明細を再読込
        </button>}
        {previewError && <p className="error-message" role="alert">{previewError}</p>}
        {previewLoading && <p className="receivable-loading" role="status">Previewを取得中…</p>}

        {availableModes.length > 0 && <div className="receivable-mode-panel">
          <span>処理方法</span>
          <div>{availableModes.map((availableMode) => <button type="button"
            className={mode === availableMode ? "selected" : ""}
            key={availableMode} onClick={() => changeMode(availableMode)} disabled={previewLoading}>
            {modeLabels[availableMode]}
          </button>)}</div>
        </div>}

        {showDifferenceAccount && <div className="receivable-difference-panel">
          <label>差額科目<select value={differenceAccount} onChange={(event) => chooseDifferenceAccount(event.target.value)}
            disabled={previewLoading || mastersLoading || !masters || Boolean(mastersError)}>
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
            onChange={(event) => { setDifferenceSummary(event.target.value); setPreview(null); setPreviewError(null); }}
            disabled={previewLoading} placeholder="空欄時はBackend既定値" /></label>
          {recommendations.length > 0 && <div className="receivable-recommendations">
            <span>推奨（Backend順位）</span>
            {recommendations.map((account) => <button type="button" key={account.code}
              onClick={() => chooseDifferenceAccount(account.name)} disabled={previewLoading}>
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
          </section> : <p className="notice notice-warning">必要な差額科目を選択すると、仕訳Previewを確認できます。</p>}
          <p className="receivable-revision" title={preview.ledger_revision}>
            Preview revision: {preview.ledger_revision.slice(0, 12)}…
          </p>
        </div>}
      </section>
    </section>
  );
}
