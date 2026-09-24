import type {
  ReceivableImportInput,
  ReceivableImportPreview,
  ReceivableImportResult,
  ReceivableCustomerDetailResponse,
  ReceivableOptionsResponse,
  ReceivablePreviewRequest,
  ReceivablePreviewResponse,
  ReceivableRegistrationHandoffResponse,
  ReceivableSettlementExecuteRequest,
  ReceivableSettlementExecuteResponse,
  ReceivableSummaryResponse,
} from "../types/receivable";

function importForm(input: ReceivableImportInput): FormData {
  const form = new FormData();
  form.append("file", input.file);
  form.append("invoice_date", input.invoice_date);
  form.append("default_account", input.default_account);
  form.append("department", input.department);
  if (input.payment_due_date) form.append("payment_due_date", input.payment_due_date);
  return form;
}

export function previewReceivableImport(input: ReceivableImportInput): Promise<ReceivableImportPreview> {
  return requestReceivable("/api/receivables/import/preview", { method: "POST", body: importForm(input) });
}

export function executeReceivableImport(input: ReceivableImportInput): Promise<ReceivableImportResult> {
  return requestReceivable("/api/receivables/import/execute", { method: "POST", body: importForm(input) });
}

const statusFallbacks: Record<number, string> = {
  404: "指定した取引先の未収データがありません。再読込してください。",
  409: "未収データが更新されています。内容を再確認してください。",
  422: "入力内容を確認してください。",
  423: "未収台帳をほかの処理が使用中です。時間をおいて再試行してください。",
  503: "未収台帳の状態を確認する必要があります。",
};

export class ReceivableApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ReceivableApiError";
    this.status = status;
  }
}

async function getSafeErrorMessage(response: Response): Promise<string> {
  try {
    const body = await response.json() as { detail?: unknown };
    if (typeof body.detail === "string" && body.detail.trim()) {
      return body.detail;
    }
  } catch {
    // 500系のraw responseは表示せず、status別の安全な文言を使用する。
  }
  return statusFallbacks[response.status] ?? "未収データを取得できませんでした。";
}

async function requestReceivable<T>(
  input: string,
  init?: RequestInit,
): Promise<T> {
  const response = await fetch(input, init);
  if (!response.ok) {
    throw new ReceivableApiError(
      response.status,
      await getSafeErrorMessage(response),
    );
  }
  return response.json() as Promise<T>;
}

export function fetchReceivableSummary(): Promise<ReceivableSummaryResponse> {
  return requestReceivable<ReceivableSummaryResponse>("/api/receivables/summary");
}

export function fetchReceivableDetail(
  customerName: string,
): Promise<ReceivableCustomerDetailResponse> {
  const query = new URLSearchParams({ customer_name: customerName });
  return requestReceivable<ReceivableCustomerDetailResponse>(
    `/api/receivables/customers/detail?${query.toString()}`,
  );
}

export function fetchReceivableOptions(): Promise<ReceivableOptionsResponse> {
  return requestReceivable<ReceivableOptionsResponse>("/api/receivables/options");
}

export function previewReceivableSettlement(
  request: ReceivablePreviewRequest,
): Promise<ReceivablePreviewResponse> {
  return requestReceivable<ReceivablePreviewResponse>(
    "/api/receivables/preview-settlement",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    },
  );
}

export function executeReceivableSettlement(
  request: ReceivableSettlementExecuteRequest,
): Promise<ReceivableSettlementExecuteResponse> {
  return requestReceivable<ReceivableSettlementExecuteResponse>(
    "/api/receivables/execute-settlement",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    },
  );
}

export function prepareReceivableRegistration(
  settlementId: string,
  receiptRef: string,
): Promise<ReceivableRegistrationHandoffResponse> {
  return requestReceivable<ReceivableRegistrationHandoffResponse>(
    `/api/receivables/settlements/${encodeURIComponent(settlementId)}/prepare-registration`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ receipt_ref: receiptRef }),
    },
  );
}
