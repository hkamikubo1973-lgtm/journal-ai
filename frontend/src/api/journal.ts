import type {
  EpsonExportCsvRequest,
  EpsonSaveCsvResponse,
  InputExcelRequest,
  InputExcelSaveResponse,
  PrepareRegistrationRequest,
  PrepareRegistrationResponse,
  JournalMastersResponse,
  JournalSearchRequest,
  JournalSearchResponse,
  RegistrationCartItem,
} from "../types/journal";

export function buildEpsonExportRequest(cart: readonly RegistrationCartItem[]): EpsonExportCsvRequest {
  return {
    items: cart.map((item) => item.source_type === "receivable_settlement" ? {
      source_type: "receivable_settlement",
      provenance: {
        settlement_id: item.provenance.settlement_id,
        receipt_ref: item.provenance.receipt_ref,
        row_index: item.provenance.row_index,
        row_count: item.provenance.row_count,
        settlement_row_id: item.provenance.settlement_row_id,
      },
    } : {
      registration_id: item.registration_id,
      prepared_journal: item.prepared_journal,
      epson_base_row: item.epson_base_row,
    }),
  };
}

const epsonReasonMessages: Record<string, string> = {
  template_not_found: "EPSON変換に使える過去仕訳が見つかりません",
  template_ambiguous: "過去仕訳に複数のEPSON設定があり自動決定できません",
  template_invalid: "EPSON変換用の過去仕訳を利用できません",
  receipt_invalid: "未収消込データを確認できません",
  provenance_mismatch: "未収消込データを確認できません",
  master_validation_failed: "現在のマスターと仕訳内容が一致しません",
  prepared_journal_invalid: "仕訳内容をEPSON出力用に準備できません",
};

async function getEpsonErrorMessage(response: Response, request: EpsonExportCsvRequest): Promise<string> {
  const fallback = "EPSON出力に失敗しました。仕訳内容を確認して再度お試しください。";
  try {
    const body = await response.json() as { detail?: unknown };
    const detail = body.detail;
    if (detail && typeof detail === "object" && "code" in detail && typeof detail.code === "string") {
      return Object.hasOwn(epsonReasonMessages, detail.code) ? epsonReasonMessages[detail.code] : fallback;
    }
    if (request.items.some((item) => item.source_type === "receivable_settlement")) {
      // B2-K receipt/readiness errors use HTTP status and a string detail.
      if ([404, 409, 422, 503].includes(response.status)) return "未収消込データを確認できません";
      if (response.status === 423) return "未収台帳をほかの処理が使用中です。しばらくしてから再度お試しください。";
      return fallback;
    }
    // Preserve searched-journal validation messages, never raw response bodies.
    if (typeof detail === "string" && (
      /^\d+件目の(?:prepared_journalがありません|登録予定が不正です|registration_idがありません|registration_idが内容と一致しません)。$/.test(detail)
      || /^\d+件目のprepared_journalに必要な項目が不足しています: [a-z_、]+$/.test(detail)
      || /^\d+件目: (?:正式EPSON生成元行がありません。|EPSON45列を確認できません。|正式EPSON生成元行に必要な45列が不足しています: [ぁ-んァ-ヶ一-龠０-９、]+)$/.test(detail)
      || [
        "EPSON CSVの出力対象がありません。",
        "EPSON CSVをCP932へ変換できない文字が含まれています。",
        "EPSON CSVを生成できませんでした。",
        "EPSON CSVの保存処理に失敗しました。検索DBを確認してください。",
        "EPSON CSVの正式保存処理に失敗しました。検索DBを確認してください。",
      ].includes(detail)
    )) return detail;
  } catch {
    // Malformed/HTML/internal error responses are not user-facing messages.
  }
  return fallback;
}

export type DownloadedFile = {
  blob: Blob;
  filename: string;
};

function getDownloadFilename(
  contentDisposition: string | null,
  fallback: string,
): string {
  const match = contentDisposition?.match(/filename="?([^";]+)"?/i);
  return match?.[1] || fallback;
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
    throw new Error(`EPSON CSVダウンロードエラー: ${await getEpsonErrorMessage(response, request)}`);
  }

  return {
    blob: await response.blob(),
    filename: getDownloadFilename(
      response.headers.get("Content-Disposition"),
      "epson_output.csv",
    ),
  };
}

export async function saveEpsonCsv(
  request: EpsonExportCsvRequest,
): Promise<EpsonSaveCsvResponse> {
  const response = await fetch("/api/journal/save-epson-csv", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(request),
  });

  if (!response.ok) {
    throw new Error(`EPSON CSV正式保存エラー: ${await getEpsonErrorMessage(response, request)}`);
  }

  return response.json() as Promise<EpsonSaveCsvResponse>;
}

export async function downloadInputExcel(
  request: InputExcelRequest,
): Promise<DownloadedFile> {
  const response = await fetch("/api/journal/export-input-excel", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(request),
  });

  if (!response.ok) {
    throw new Error(`入力用Excelダウンロードエラー: ${await getErrorMessage(response)}`);
  }

  return {
    blob: await response.blob(),
    filename: getDownloadFilename(
      response.headers.get("Content-Disposition"),
      "input_journal_print.xlsx",
    ),
  };
}

export async function saveInputExcel(
  request: InputExcelRequest,
): Promise<InputExcelSaveResponse> {
  const response = await fetch("/api/journal/save-input-excel", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(request),
  });

  if (!response.ok) {
    throw new Error(`入力用Excel保存エラー: ${await getErrorMessage(response)}`);
  }

  return response.json() as Promise<InputExcelSaveResponse>;
}

export async function fetchJournalMasters(): Promise<JournalMastersResponse> {
  const response = await fetch("/api/journal/masters");

  if (!response.ok) {
    const text = await response.text();
    throw new Error(`マスター取得APIエラー: ${response.status} ${text}`);
  }

  return response.json() as Promise<JournalMastersResponse>;
}
