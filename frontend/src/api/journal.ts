import type {
  EpsonExportCsvRequest,
  PrepareRegistrationRequest,
  PrepareRegistrationResponse,
  JournalMastersResponse,
  JournalSearchRequest,
  JournalSearchResponse,
} from "../types/journal";

export type DownloadedFile = {
  blob: Blob;
  filename: string;
};

function getDownloadFilename(contentDisposition: string | null): string {
  const match = contentDisposition?.match(/filename="?([^";]+)"?/i);
  return match?.[1] || "epson_output.csv";
}

async function getErrorMessage(response: Response): Promise<string> {
  const text = await response.text();
  try {
    const body = JSON.parse(text) as { detail?: unknown };
    if (typeof body.detail === "string") return body.detail;
  } catch {
    // JSON以外のエラー本文は下のfallbackで表示する。
  }
  return text || `HTTP ${response.status}`;
}

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

export async function downloadEpsonCsv(
  request: EpsonExportCsvRequest,
): Promise<DownloadedFile> {
  const response = await fetch("/api/journal/export-epson-csv", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(request),
  });

  if (!response.ok) {
    throw new Error(`EPSON CSVダウンロードエラー: ${await getErrorMessage(response)}`);
  }

  return {
    blob: await response.blob(),
    filename: getDownloadFilename(response.headers.get("Content-Disposition")),
  };
}

export async function fetchJournalMasters(): Promise<JournalMastersResponse> {
  const response = await fetch("/api/journal/masters");

  if (!response.ok) {
    const text = await response.text();
    throw new Error(`マスター取得APIエラー: ${response.status} ${text}`);
  }

  return response.json() as Promise<JournalMastersResponse>;
}
