export type OutputSettings = { csv_export_dir: string };

export async function getOutputSettings(): Promise<OutputSettings> {
  const response = await fetch("/api/settings/output");
  if (!response.ok) throw new Error("現在の保存先設定を取得できませんでした。");
  return response.json() as Promise<OutputSettings>;
}

export async function saveOutputSettings(csvExportDir: string): Promise<OutputSettings> {
  const response = await fetch("/api/settings/output", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ csv_export_dir: csvExportDir.trim() }),
  });
  if (!response.ok) throw new Error(response.status === 422
    ? "保存先フォルダの入力内容を確認してください。設定は変更されていません。"
    : "保存先フォルダを保存できませんでした。設定を確認して再試行してください。"
  );
  return response.json() as Promise<OutputSettings>;
}
