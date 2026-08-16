export type JournalSearchRequest = {
  keyword: string;
  department: string | null;
  amount: number | null;
  limit: 5 | 10 | 20;
};

export type JournalCandidate = {
  rank: number;
  score: number;
  pattern_key: string[];
  pattern_rank: number | null;
  search_reason: string[];
  matched_amount_row: Record<string, unknown> | null;
  source_rows: Record<string, unknown>[];
  editable_rows: Record<string, unknown>[];
  block_rows: Record<string, unknown>[];
  has_fukugo: boolean;
  has_sundry: boolean;
  contains_fukugo_or_sundry: boolean;
  show_block_rows: boolean;
  is_complex: boolean;
};

export type JournalSearchResponse = {
  query: JournalSearchRequest;
  count: number;
  candidates: JournalCandidate[];
};

export type JournalEditForm = {
  voucherDate: string;
  debitAccountCode: string;
  debitAccountName: string;
  debitSubCode: string;
  debitSubName: string;
  debitDeptCode: string;
  debitDeptName: string;
  amount: string;
  debitAmount: string;
  creditAccountCode: string;
  creditAccountName: string;
  creditSubCode: string;
  creditSubName: string;
  creditDeptCode: string;
  creditDeptName: string;
  creditAmount: string;
  summary: string;
  voucherSummary: string;
  voucherNo: string;
};
