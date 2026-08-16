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

export type PrepareRegistrationRequest = {
  edit_form: {
    voucher_date: string;
    voucher_no: string | null;
    voucher_summary: string | null;
    debit_account_code: string;
    debit_account_name: string | null;
    debit_sub_code: string | null;
    debit_sub_name: string | null;
    debit_dept_code: string | null;
    debit_dept_name: string | null;
    credit_account_code: string;
    credit_account_name: string | null;
    credit_sub_code: string | null;
    credit_sub_name: string | null;
    credit_dept_code: string | null;
    credit_dept_name: string | null;
    amount: string;
    summary: string | null;
    source_debit_amount: string | null;
    source_credit_amount: string | null;
  };
  candidate_meta: {
    rank: number | null;
    score: number | null;
    pattern_key: string[];
    pattern_rank: number | null;
    editable_row_count: number;
    source_row_count: number;
    block_row_count: number;
    has_fukugo: boolean;
    has_sundry: boolean;
    contains_fukugo_or_sundry: boolean;
    show_block_rows: boolean;
    is_complex: boolean;
  };
};

export type PreparedJournal = {
  voucher_date: string;
  voucher_no: string;
  voucher_summary: string;
  debit_account_code: string;
  debit_account_name: string;
  debit_sub_code: string;
  debit_sub_name: string;
  debit_dept_code: string;
  debit_dept_name: string;
  credit_account_code: string;
  credit_account_name: string;
  credit_sub_code: string;
  credit_sub_name: string;
  credit_dept_code: string;
  credit_dept_name: string;
  amount: number;
  summary: string;
  source_debit_amount: string;
  source_credit_amount: string;
};

export type EpsonPreviewRow = Record<string, string>;

export type PrepareRegistrationResponse = {
  ok: boolean;
  blocked: boolean;
  errors: string[];
  warnings: string[];
  registration_id: string | null;
  prepared_journal: PreparedJournal | null;
  epson_preview_row: EpsonPreviewRow | null;
};
