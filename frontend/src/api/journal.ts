import type {
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
