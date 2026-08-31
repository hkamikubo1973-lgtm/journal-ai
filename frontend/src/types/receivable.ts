export type ReceivableLedgerStatus =
  | "ready"
  | "recovery_pending"
  | "recovery_required";

export type ReceivableCustomerSummaryItem = {
  customer_name: string;
  outstanding_count: number;
  outstanding_balance: number;
};

export type ReceivableSummaryResponse = {
  ledger_revision: string;
  ledger_status: ReceivableLedgerStatus;
  settlement_available: boolean;
  customer_count: number;
  outstanding_count: number;
  outstanding_balance: number;
  customers: ReceivableCustomerSummaryItem[];
};

export type ReceivableDetailItem = {
  code: string;
  receivable_id: string;
  customer_name: string;
  billing_date: string;
  planned_payment_date: string;
  receivable_account: string;
  receivable_sub_account: string;
  department: string;
  summary: string;
  billed_amount: number;
  paid_amount: number;
  balance: number;
  status: string;
};

export type ReceivableCustomerDetailResponse = {
  ledger_revision: string;
  ledger_status: ReceivableLedgerStatus;
  settlement_available: boolean;
  customer_name: string;
  outstanding_count: number;
  outstanding_balance: number;
  receivables: ReceivableDetailItem[];
};

export type ReceivableAccountOption = {
  code: string;
  name: string;
};

export type ReceivableOptionsResponse = {
  receipt_accounts: ReceivableAccountOption[];
  default_receipt_account: string | null;
};

export type ReceivablePreviewMode = "partial" | "difference_account";

export type ReceivablePreviewPattern =
  | "exact_match"
  | "partial_settlement"
  | "shortage_difference"
  | "overpayment";

export type ReceivablePreviewRequest = {
  ledger_revision: string;
  customer_name: string;
  settlement_date: string;
  payment_amount: number;
  receipt_account: string;
  mode: ReceivablePreviewMode | null;
  difference_account: string | null;
  difference_summary: string | null;
};

export type ReceivablePreviewCandidate = {
  code: string;
  receivable_id: string;
  billing_date: string;
  billed_amount: number;
  balance: number;
  scheduled_amount: number;
  receivable_account: string;
  receivable_sub_account: string;
  department: string;
  customer_name: string;
  summary: string;
};

export type ReceivablePreviewJournalRow = {
  debit_account: string;
  credit_account: string;
  credit_sub_account: string;
  department: string;
  amount: number;
  summary: string;
};

export type ReceivableRecommendedAccount = {
  code: string;
  name: string;
};

export type ReceivablePreviewResponse = {
  ledger_revision: string;
  customer_name: string;
  settlement_date: string;
  payment_amount: number;
  receipt_account: string;
  total_receivable_balance: number;
  original_difference: number;
  target_total: number;
  difference: number;
  pattern: ReceivablePreviewPattern;
  mode: ReceivablePreviewMode | null;
  available_modes: ReceivablePreviewMode[];
  difference_account_required: boolean;
  preview_complete: boolean;
  source_candidates: ReceivablePreviewCandidate[];
  rows: ReceivablePreviewJournalRow[];
  recommended_difference_accounts: ReceivableRecommendedAccount[];
};
