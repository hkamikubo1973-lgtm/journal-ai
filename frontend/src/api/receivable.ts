import type {
  ReceivableCustomerDetailResponse,
  ReceivableOptionsResponse,
  ReceivablePreviewRequest,
  ReceivablePreviewResponse,
  ReceivableSettlementExecuteRequest,
  ReceivableSettlementExecuteResponse,
  ReceivableSummaryResponse,
} from "../types/receivable";

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
