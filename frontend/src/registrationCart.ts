import type {
  ReceivableRegistrationHandoffItem,
  RegistrationCartItem,
} from "./types/journal";

export type RegistrationCartBatchResult =
  | "added"
  | "already_added"
  | "partial_duplicate";

export function getRegistrationCartItemIdentity(item: RegistrationCartItem): string {
  return item.source_type === "searched_journal"
    ? `searched_journal:${item.registration_id}`
    : `receivable_settlement:${item.provenance.settlement_row_id}`;
}

export function addReceivableSettlementToCart(
  current: RegistrationCartItem[],
  incoming: ReceivableRegistrationHandoffItem[],
): { items: RegistrationCartItem[]; result: RegistrationCartBatchResult } {
  const currentIdentities = new Set(current.map(getRegistrationCartItemIdentity));
  const duplicateCount = incoming.filter((item) => currentIdentities.has(
    `receivable_settlement:${item.provenance.settlement_row_id}`,
  )).length;

  if (duplicateCount === incoming.length) {
    return { items: current, result: "already_added" };
  }
  if (duplicateCount > 0) {
    return { items: current, result: "partial_duplicate" };
  }

  const addedAt = new Date().toISOString();
  return {
    items: [...current, ...incoming.map((item) => ({ ...item, addedAt }))],
    result: "added",
  };
}

export function removeRegistrationCartGroup(
  current: RegistrationCartItem[],
  identity: string,
): RegistrationCartItem[] {
  const target = current.find((item) => getRegistrationCartItemIdentity(item) === identity);
  if (!target) return current;
  if (target.source_type === "searched_journal") {
    return current.filter((item) => getRegistrationCartItemIdentity(item) !== identity);
  }
  return current.filter((item) => item.source_type !== "receivable_settlement"
    || item.provenance.settlement_id !== target.provenance.settlement_id);
}
