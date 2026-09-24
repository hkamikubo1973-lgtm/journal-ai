export type JournalImportResult = {
  detected_encoding: string | null;
  column_count: number;
  uploaded_count: number;
  new_count: number;
  duplicate_count: number;
  preview_rows: Record<string, string>[];
  errors: string[];
  imported_count: number;
};

export async function importJournalCsv(file: File, action: "preview" | "execute"): Promise<JournalImportResult> {
  const body = new FormData();
  body.append("file", file);
  const response = await fetch(`/api/journal/import/${action}`, { method: "POST", body });
  if (!response.ok) {
    if (response.status === 400) throw new Error("既存の検索DBがありません。");
    throw new Error("検索DBの取込処理に失敗しました。DBを確認して再試行してください。");
  }
  return response.json();
}
