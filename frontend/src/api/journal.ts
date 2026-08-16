import type {
  PrepareRegistrationRequest,
  PrepareRegistrationResponse,
  JournalMastersResponse,
  JournalSearchRequest,
  JournalSearchResponse,
} from "../types/journal";

export async function searchJournals(
  request: JournalSearchRequest,
): Promise<JournalSearchResponse> {
  const response = await fetch("/api/journal/search", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(request),
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(`検索APIエラー: ${response.status} ${text}`);
  }

  return response.json() as Promise<JournalSearchResponse>;
}

export async function prepareRegistration(
  request: PrepareRegistrationRequest,
): Promise<PrepareRegistrationResponse> {
  const response = await fetch("/api/journal/prepare-registration", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(request),
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(`登録準備APIエラー: ${response.status} ${text}`);
  }

  return response.json() as Promise<PrepareRegistrationResponse>;
}

export async function fetchJournalMasters(): Promise<JournalMastersResponse> {
  const response = await fetch("/api/journal/masters");

  if (!response.ok) {
    const text = await response.text();
    throw new Error(`マスター取得APIエラー: ${response.status} ${text}`);
  }

  return response.json() as Promise<JournalMastersResponse>;
}
