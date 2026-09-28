import type { JournalAiAssistResponse, JournalAiAssistState, JournalSearchRequest } from "../types/journal";

const states: JournalAiAssistState[] = ["QUEUED", "RUNNING", "COMPLETED", "FAILED"];

export class JournalAiAssistApiError extends Error {}

function responseError(status: number): JournalAiAssistApiError {
  if (status === 503) return new JournalAiAssistApiError("AI補助に接続できませんでした。通常の検索機能はそのまま利用できます。");
  if (status === 504) return new JournalAiAssistApiError("AI補助の応答がタイムアウトしました。");
  if (status === 502) return new JournalAiAssistApiError("AI補助の結果を取得できませんでした。");
  return new JournalAiAssistApiError("AI補助の結果を取得できませんでした。");
}

function parseResponse(value: unknown, completedContentRequired: boolean): JournalAiAssistResponse {
  if (!value || typeof value !== "object") throw responseError(0);
  const item = value as Record<string, unknown>;
  if (typeof item.job_id !== "string" || !item.job_id || !states.includes(item.state as JournalAiAssistState)) {
    throw responseError(0);
  }
  if (completedContentRequired && item.state === "COMPLETED" && (typeof item.content !== "string" || !item.content.trim())) {
    throw responseError(0);
  }
  return { job_id: item.job_id, state: item.state as JournalAiAssistState,
    ...(typeof item.content === "string" ? { content: item.content } : {}) };
}

async function getJson(response: Response, completedContentRequired: boolean): Promise<JournalAiAssistResponse> {
  if (!response.ok) throw responseError(response.status);
  let body: unknown;
  try { body = await response.json(); }
  catch { throw responseError(0); }
  return parseResponse(body, completedContentRequired);
}

export async function submitJournalAiAssist(request: JournalSearchRequest): Promise<JournalAiAssistResponse> {
  let response: Response;
  try {
    response = await fetch("/api/journal/ai-assist", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(request),
    });
  } catch { throw responseError(503); }
  return getJson(response, false);
}

export async function getJournalAiAssist(jobId: string): Promise<JournalAiAssistResponse> {
  let response: Response;
  try { response = await fetch(`/api/journal/ai-assist/${encodeURIComponent(jobId)}`); }
  catch { throw responseError(503); }
  const job = await getJson(response, true);
  if (job.job_id !== jobId) throw responseError(0);
  return job;
}
