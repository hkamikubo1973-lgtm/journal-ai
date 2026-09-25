export type MasterKind = "account" | "department" | "sub";
export type MasterUpdatePreview = {
  kind: MasterKind;
  current_count: number;
  found_count: number;
  added_count: number;
  unchanged_count: number;
  conflict_count: number;
  added_items: Record<string, string>[];
  conflicts: { reason: string; code?: string; name?: string; account_code?: string; sub_code?: string; sub_name?: string }[];
  sub_added_count?: number;
  relation_added_count?: number;
  relation_added_items?: Record<string, string>[];
  applied: boolean;
};
export type AccountInput = { code: string; name: string; category: string; add_to_payment: boolean };

async function request<T>(route: string, body: unknown): Promise<T> {
  const response = await fetch(`/api/journal/masters/${route}`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (!response.ok) {
    const data = await response.json().catch(() => null);
    const allowed = ["科目コード・科目名・分類を確認してください。", "同じ科目コードに別の名称が登録されています。",
      "同じ名称に複数の科目コードがあるため入金科目候補へ追加できません。", "入金科目候補として利用できない名称です。",
      "マスターの列構造を確認してください。", "マスターの行構造を確認してください。", "検索DBの列構造を確認してください。"];
    throw new Error(allowed.includes(data?.detail) ? data.detail : "マスター処理に失敗しました。ファイルを確認してください。");
  }
  return response.json();
}

export function updateMasters(kind: MasterKind, execute = false) {
  return request<MasterUpdatePreview>(execute ? "update" : "update-preview", { kind });
}
export function addAccount(input: AccountInput) {
  return request<{ account_added: boolean; payment_added: boolean; message: string }>("accounts/add", {
    code: input.code, name: input.name, category: input.category, add_to_payment: input.add_to_payment,
  });
}
