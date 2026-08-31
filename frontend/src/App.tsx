import { useEffect, useState, type FormEvent, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from "react";
import {
  downloadEpsonCsv,
  downloadInputExcel,
  fetchJournalMasters,
  prepareRegistration,
  saveEpsonCsv,
  saveInputExcel,
  searchJournals,
} from "./api/journal";
import type {
  JournalCandidate,
  JournalEditForm,
  JournalMastersResponse,
  JournalSearchRequest,
  JournalSearchResponse,
  PrepareRegistrationRequest,
  PrepareRegistrationResponse,
  RegistrationCartItem,
  SubAccountRelation,
} from "./types/journal";
import ReceivableWorkspace from "./components/receivable/ReceivableWorkspace";

type Workspace = "journal" | "receivable";

function WorkspaceTabs({ active, onChange }: {
  active: Workspace;
  onChange: (workspace: Workspace) => void;
}) {
  return (
    <nav className="workspace-tabs" aria-label="業務画面" role="tablist">
      <button type="button" role="tab" aria-selected={active === "journal"}
        className={active === "journal" ? "active" : ""} onClick={() => onChange("journal")}>
        通常仕訳
      </button>
      <button type="button" role="tab" aria-selected={active === "receivable"}
        className={active === "receivable" ? "active" : ""} onClick={() => onChange("receivable")}>
        未収消込
      </button>
    </nav>
  );
}

const blockRowFields = [
  { key: "date", label: "日付", amount: false },
  { key: "debit", label: "借方", amount: false },
  { key: "credit", label: "貸方", amount: false },
  { key: "debit_sub", label: "借方補助", amount: false },
  { key: "credit_sub", label: "貸方補助", amount: false },
  { key: "debit_amount", label: "借方金額", amount: true },
  { key: "credit_amount", label: "貸方金額", amount: true },
  { key: "summary", label: "摘要", amount: false },
] as const;

type EditFormField = {
  key: keyof JournalEditForm;
  label: string;
  tabOrder: number;
  wide?: boolean;
  amount?: boolean;
};

type MasterCheckMessage = {
  level: "ok" | "warning" | "error";
  message: string;
};

const commonFields: EditFormField[] = [
  { key: "voucherNo", label: "伝票番号", tabOrder: 3 },
  { key: "voucherSummary", label: "伝票摘要", tabOrder: 4, wide: true },
  { key: "amount", label: "金額", tabOrder: 5, amount: true },
  { key: "summary", label: "摘要", tabOrder: 6, wide: true },
];
const debitFields: EditFormField[] = [];
const creditFields: EditFormField[] = [];

const epsonPreviewFields = [
  "伝票日付", "証番号", "借方科目", "借方科目名", "借方補助", "借方補助科目名", "借方金額",
  "貸方科目", "貸方科目名", "貸方補助", "貸方補助科目名", "貸方金額", "摘要", "伝票摘要",
] as const;

type AppTabAttribute = "data-search-tab" | "data-candidate-tab" | "data-journal-tab" | "data-cart-tab";

const appTabGroups: AppTabAttribute[] = [
  "data-search-tab",
  "data-candidate-tab",
  "data-journal-tab",
  "data-cart-tab",
];

function handleAppTabKeyDown(event: ReactKeyboardEvent<HTMLElement>): void {
  if (event.key !== "Tab" || event.altKey || event.ctrlKey || event.metaKey) return;

  const controls = appTabGroups.flatMap((attribute) => {
    const groupControls = Array.from(event.currentTarget.querySelectorAll<HTMLElement>(`[${attribute}]`))
      .filter((element) => {
        if (element.matches(":disabled") || element.getClientRects().length === 0) return false;
        return window.getComputedStyle(element).visibility !== "hidden";
      });

    if (attribute === "data-search-tab" || attribute === "data-journal-tab") {
      groupControls.sort((left, right) => Number(left.getAttribute(attribute)) - Number(right.getAttribute(attribute)));
    }
    return groupControls;
  });

  if (controls.length === 0) return;

  event.preventDefault();
  const currentIndex = event.target instanceof HTMLElement ? controls.indexOf(event.target) : -1;
  const nextIndex = currentIndex < 0
    ? event.shiftKey ? controls.length - 1 : 0
    : (currentIndex + (event.shiftKey ? -1 : 1) + controls.length) % controls.length;
  controls[nextIndex].focus();
}

function getString(row: Record<string, unknown>, key: string): string {
  const value = row[key];
  if (value === null || value === undefined) return "";
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}

type VoucherDateParts = {
  year: number;
  month: number;
  day: number;
};

function getDaysInMonth(year: number, month: number): number {
  const monthDays = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  if (month !== 2) return monthDays[month - 1] ?? 0;
  const isLeapYear = year % 400 === 0 || (year % 4 === 0 && year % 100 !== 0);
  return isLeapYear ? 29 : 28;
}

function parseVoucherDate(value: string): VoucherDateParts | null {
  const trimmed = value.trim();
  const compact = /^\d{8}$/.test(trimmed)
    ? trimmed
    : /^(\d{4})-(\d{2})-(\d{2})$/.exec(trimmed)?.slice(1).join("");
  if (!compact) return null;

  const year = Number(compact.slice(0, 4));
  const month = Number(compact.slice(4, 6));
  const day = Number(compact.slice(6, 8));
  if (year < 1 || month < 1 || month > 12 || day < 1 || day > getDaysInMonth(year, month)) return null;
  return { year, month, day };
}

function formatVoucherDate({ year, month, day }: VoucherDateParts): string {
  return `${String(year).padStart(4, "0")}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
}

function normalizeVoucherDate(value: string): string {
  const parts = parseVoucherDate(value);
  return parts ? formatVoucherDate(parts) : "";
}

function changeVoucherMonth(value: string, yearMonth: string): string {
  const match = /^(\d{4})-(\d{2})$/.exec(yearMonth);
  if (!match) return "";
  const year = Number(match[1]);
  const month = Number(match[2]);
  if (year < 1 || month < 1 || month > 12) return "";

  const current = parseVoucherDate(value);
  const day = Math.min(current?.day ?? 1, getDaysInMonth(year, month));
  return formatVoucherDate({ year, month, day });
}

function changeVoucherDay(value: string, dayValue: string): string {
  const current = parseVoucherDate(value);
  const day = Number(dayValue);
  if (!current || !Number.isInteger(day) || day < 1 || day > getDaysInMonth(current.year, current.month)) return value;
  return formatVoucherDate({ ...current, day });
}

function buildEditFormFromRow(row: Record<string, unknown>): JournalEditForm {
  const debitAmount = getString(row, "借方金額");
  const creditAmount = getString(row, "貸方金額");
  return {
    voucherDate: normalizeVoucherDate(getString(row, "伝票日付")), voucherNo: getString(row, "証番号"),
    voucherSummary: getString(row, "伝票摘要"), debitAccountCode: getString(row, "借方科目"),
    debitAccountName: getString(row, "借方科目名"), debitSubCode: getString(row, "借方補助"),
    debitSubName: getString(row, "借方補助科目名"), debitDeptCode: getString(row, "借方部門"),
    debitDeptName: getString(row, "借方部門名"), amount: chooseCommonAmount(debitAmount, creditAmount),
    debitAmount,
    creditAccountCode: getString(row, "貸方科目"), creditAccountName: getString(row, "貸方科目名"),
    creditSubCode: getString(row, "貸方補助"), creditSubName: getString(row, "貸方補助科目名"),
    creditDeptCode: getString(row, "貸方部門"), creditDeptName: getString(row, "貸方部門名"),
    creditAmount, summary: getString(row, "摘要"),
  };
}

function chooseCommonAmount(debitAmount: string, creditAmount: string): string {
  return debitAmount || creditAmount || "";
}

function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "-";
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}

function parseAmount(value: string): number | null {
  const normalized = value.replace(/[,\s円]/g, "");
  if (normalized === "") return null;
  const amount = Number(normalized);
  return Number.isFinite(amount) ? amount : null;
}

function areSourceAmountsEqual(form: JournalEditForm | null): boolean | null {
  if (!form || !form.debitAmount || !form.creditAmount) return null;
  const debitAmount = parseAmount(form.debitAmount);
  const creditAmount = parseAmount(form.creditAmount);
  if (debitAmount === null || creditAmount === null) return null;
  return debitAmount === creditAmount;
}

function formatAmount(value: string): string {
  const amount = parseAmount(value);
  return amount === null ? (value || "-") : amount.toLocaleString("ja-JP");
}

function formatAmountWithUnit(value: string): string {
  const formatted = formatAmount(value);
  return formatted === "-" ? formatted : `${formatted}円`;
}

function shortId(id: string): string {
  return `${id.slice(0, 10)}…`;
}

function getPreviewValue(row: Record<string, unknown> | null | undefined, key: string): string {
  if (!row) return "-";
  return displayValue(row[key]);
}

function getCartAmount(item: RegistrationCartItem): number {
  if (Number.isFinite(item.prepared_journal.amount)) return item.prepared_journal.amount;
  const previewAmount = parseAmount(getPreviewValue(item.epson_preview_row, "借方金額"));
  return previewAmount ?? 0;
}

function formatAmountNumber(value: number): string {
  return `${value.toLocaleString("ja-JP")}円`;
}

function checkEditFormMasters(
  form: JournalEditForm | null,
  masters: JournalMastersResponse | null,
): MasterCheckMessage[] {
  if (!form || !masters) return [];

  const messages: MasterCheckMessage[] = [];
  const checkAccount = (side: "借方" | "貸方", code: string, name: string) => {
    if (!code.trim()) {
      messages.push({ level: "error", message: `${side}科目コードが未入力です。` });
      return;
    }
    const matches = masters.accounts.filter((account) => account.code === code.trim());
    if (matches.length === 0) {
      messages.push({ level: "error", message: `${side}科目コード ${code} はマスターに存在しません。` });
      return;
    }
    const account = matches[0];
    const hasWarning = account.name !== name.trim() || !account.selectable;
    if (account.name !== name.trim()) {
      messages.push({ level: "warning", message: `${side}科目コード ${code} のマスター名称とフォーム名称が一致しません。` });
    }
    if (!account.selectable) {
      messages.push({
        level: "warning",
        message: `${side}科目コード ${code} は通常選択不可です: ${account.unselectable_reason ?? "直接選択対象外です。"}`,
      });
    }
    if (!hasWarning) messages.push({ level: "ok", message: `${side}科目 ${code}（${name}）はマスターに存在します。` });
  };

  const checkSubAccount = (
    side: "借方" | "貸方",
    accountCode: string,
    code: string,
    name: string,
  ) => {
    const normalizedCode = code.trim();
    const normalizedName = name.trim();
    if (!normalizedCode && !normalizedName) {
      messages.push({ level: "ok", message: `${side}補助は未指定です。` });
      return;
    }
    if (!normalizedCode || !normalizedName) {
      messages.push({ level: "error", message: `${side}補助はコードと名称を一組で指定してください。` });
      return;
    }
    const relation = masters.sub_account_relations.find((item) =>
      item.account_code === accountCode.trim() && item.sub_code === normalizedCode
    );
    if (!relation) {
      messages.push({
        level: "warning",
        message: `${side}補助 ${normalizedCode}（${normalizedName}）は現在の補助親子関係マスターに存在しません。補助を選び直してください。`,
      });
      return;
    }
    if (relation.sub_name !== normalizedName) {
      messages.push({
        level: "warning",
        message: `${side}補助 ${normalizedCode} は現在「${relation.sub_name}」です。登録準備時に現在名称へ更新されます。`,
      });
      return;
    }
    messages.push({
      level: "ok",
      message: `${side}科目 ${accountCode.trim()} で補助 ${normalizedCode}（${relation.sub_name}）を使用できます。`,
    });
  };

  const checkDepartment = (side: "借方" | "貸方", code: string, name: string) => {
    if (!code.trim()) {
      messages.push({ level: "ok", message: `${side}部門は未指定です。` });
      return;
    }
    const matches = masters.departments.filter((department) => department.code === code.trim());
    if (matches.length === 0) {
      messages.push({ level: "error", message: `${side}部門コード ${code} はマスターに存在しません。` });
      return;
    }
    if (!matches.some((department) => department.name === name.trim())) {
      messages.push({ level: "warning", message: `${side}部門コード ${code} は存在しますが、マスター名称とフォーム名称が一致しません。` });
      return;
    }
    messages.push({ level: "ok", message: `${side}部門 ${code}（${name}）はマスターに存在します。` });
  };

  checkAccount("借方", form.debitAccountCode, form.debitAccountName);
  checkAccount("貸方", form.creditAccountCode, form.creditAccountName);
  checkSubAccount("借方", form.debitAccountCode, form.debitSubCode, form.debitSubName);
  checkSubAccount("貸方", form.creditAccountCode, form.creditSubCode, form.creditSubName);
  checkDepartment("借方", form.debitDeptCode, form.debitDeptName);
  checkDepartment("貸方", form.creditDeptCode, form.creditDeptName);
  return messages;
}

function findAccountByCode(masters: JournalMastersResponse | null, code: string) {
  return masters?.accounts.find((account) => account.code === code.trim());
}

function findDepartmentByCode(masters: JournalMastersResponse | null, code: string) {
  return masters?.departments.find((department) => department.code === code.trim());
}

function findSubAccountRelation(
  masters: JournalMastersResponse | null,
  accountCode: string,
  subCode: string,
) {
  return masters?.sub_account_relations.find((relation) =>
    relation.account_code === accountCode.trim() && relation.sub_code === subCode.trim()
  );
}

function getSubAccountRelations(
  masters: JournalMastersResponse | null,
  accountCode: string,
) {
  return masters?.sub_account_relations.filter((relation) =>
    relation.account_code === accountCode.trim()
  ) ?? [];
}

function changeSubAccountSelection(
  form: JournalEditForm,
  side: "debit" | "credit",
  relation: SubAccountRelation | undefined,
): JournalEditForm {
  return side === "debit"
    ? {
      ...form,
      debitSubCode: relation?.sub_code ?? "",
      debitSubName: relation?.sub_name ?? "",
    }
    : {
      ...form,
      creditSubCode: relation?.sub_code ?? "",
      creditSubName: relation?.sub_name ?? "",
    };
}

function changeDepartmentSelection(
  form: JournalEditForm,
  side: "debit" | "credit",
  department: { code: string; name: string } | undefined,
): JournalEditForm {
  return side === "debit"
    ? {
      ...form,
      debitDeptCode: department?.code ?? "",
      debitDeptName: department?.name ?? "",
    }
    : {
      ...form,
      creditDeptCode: department?.code ?? "",
      creditDeptName: department?.name ?? "",
    };
}

function getCandidateSummary(candidate: JournalCandidate) {
  const editableRow = candidate.editable_rows[0] ?? {};
  const blockRow = candidate.block_rows[0] ?? {};
  const getValue = (editableKey: string, blockKey: string) =>
    getString(editableRow, editableKey) || getString(blockRow, blockKey);
  const debitAmount = getValue("借方金額", "debit_amount");
  const creditAmount = getValue("貸方金額", "credit_amount");

  return {
    debit: getValue("借方科目名", "debit") || "-",
    credit: getValue("貸方科目名", "credit") || "-",
    amount: formatAmountWithUnit(debitAmount || creditAmount),
    summary: getValue("摘要", "summary") || "-",
  };
}

function isEditFormChanged(
  current: JournalEditForm | null,
  initial: JournalEditForm | null,
): boolean {
  if (!current || !initial) return false;
  return JSON.stringify(current) !== JSON.stringify(initial);
}

function isFieldChanged(
  field: keyof JournalEditForm,
  current: JournalEditForm,
  initial: JournalEditForm | null,
): boolean {
  return initial !== null && current[field] !== initial[field];
}

function CandidateCard({ candidate, selected, onSelect }: {
  candidate: JournalCandidate; selected: boolean; onSelect: (candidate: JournalCandidate) => void;
}) {
  const journal = getCandidateSummary(candidate);

  return (
    <article className={`candidate-card clickable${selected ? " selected" : ""}`}
      aria-current={selected ? "true" : undefined} onClick={() => onSelect(candidate)}>
      <div className="candidate-card-heading">
        <div className="candidate-title-group">
          <h3>候補{candidate.rank} <span>Score {candidate.score}</span></h3>
          <div className="candidate-alert-badges">
            {candidate.has_fukugo && <span>資金複合</span>}
            {candidate.has_sundry && <span>諸口</span>}
            {candidate.contains_fukugo_or_sundry && !candidate.has_fukugo && !candidate.has_sundry && <span>資金複合・諸口を含む</span>}
            {candidate.is_complex && <span>複合仕訳</span>}
          </div>
        </div>
        {selected && <span className="selected-badge">選択中</span>}
      </div>
      <p className="candidate-journal-line">
        <strong>{journal.debit}</strong>
        <span aria-hidden="true">→</span>
        <strong>{journal.credit}</strong>
        <b>{journal.amount}</b>
      </p>
      <p className="candidate-summary-line" title={journal.summary}>{journal.summary}</p>
      <div className="candidate-card-actions">
        <div className="candidate-primary-reasons">
          <span>主な検索理由</span>
          <p title={candidate.search_reason.join(" / ")}>{candidate.search_reason.slice(0, 2).join("・") || "-"}</p>
        </div>
        <button className="candidate-select-button" type="button" data-candidate-tab="" onClick={(event) => {
          event.stopPropagation();
          onSelect(candidate);
        }}>
          {selected ? "選択中の候補" : "この候補を選択"}
        </button>
      </div>
      <details className="candidate-details" onClick={(event) => event.stopPropagation()}>
        <summary>詳細を表示</summary>
        <p className="pattern-key"><span>パターンキー:</span> {candidate.pattern_key.join(" / ") || "-"}</p>
        <dl className="candidate-facts">
          <div><dt>パターン順位</dt><dd>{candidate.pattern_rank ?? "-"}</dd></div>
          <div><dt>元データ / 編集対象 / 同一伝票ブロック</dt><dd>{candidate.source_rows.length} / {candidate.editable_rows.length} / {candidate.block_rows.length}</dd></div>
          <div><dt>金額一致行</dt><dd>{candidate.matched_amount_row ? "あり" : "なし"}</dd></div>
          <div><dt>資金複合あり</dt><dd>{candidate.has_fukugo ? "あり" : "なし"}</dd></div>
          <div><dt>諸口あり</dt><dd>{candidate.has_sundry ? "あり" : "なし"}</dd></div>
          <div><dt>資金複合・諸口を含む</dt><dd>{candidate.contains_fukugo_or_sundry ? "あり" : "なし"}</dd></div>
          <div><dt>ブロック行表示対象</dt><dd>{candidate.show_block_rows ? "はい" : "いいえ"}</dd></div>
          <div><dt>複合仕訳</dt><dd>{candidate.is_complex ? "はい" : "いいえ"}</dd></div>
        </dl>
        <div className="search-reasons"><span>検索理由（全件）</span>
          {candidate.search_reason.length > 0 ? <ul>
            {candidate.search_reason.map((reason, index) => <li key={`${reason}-${index}`}>{reason}</li>)}
          </ul> : <p>-</p>}
        </div>
        <details className="candidate-json-details"><summary>金額一致行</summary>
          <pre>{JSON.stringify(candidate.matched_amount_row, null, 2)}</pre>
        </details>
        <details className="candidate-json-details"><summary>元データ行（{candidate.source_rows.length}件）</summary>
          <pre>{JSON.stringify(candidate.source_rows, null, 2)}</pre>
        </details>
        <details className="candidate-json-details"><summary>編集対象行（{candidate.editable_rows.length}件）</summary>
          <pre>{JSON.stringify(candidate.editable_rows, null, 2)}</pre>
        </details>
        <details className="candidate-json-details"><summary>同一伝票ブロック行（{candidate.block_rows.length}件）</summary>
          <pre>{JSON.stringify(candidate.block_rows, null, 2)}</pre>
        </details>
      </details>
    </article>
  );
}

function FormSection({ title, fields, editForm, initialEditForm, onChange, children, className = "", gridClassName = "" }: {
  title: string; fields: EditFormField[]; editForm: JournalEditForm;
  initialEditForm: JournalEditForm | null;
  onChange: (key: keyof JournalEditForm, value: string) => void;
  children?: ReactNode;
  className?: string;
  gridClassName?: string;
}) {
  return (
    <fieldset className={`form-section ${className}`.trim()}><legend>{title}</legend><div className={`form-grid ${gridClassName}`.trim()}>
      {children}
      {fields.map((field) => {
        const changed = isFieldChanged(field.key, editForm, initialEditForm);
        const fieldClassName = [field.wide ? "form-field-wide" : "", changed ? "field-changed" : ""].filter(Boolean).join(" ");
        const controlClassName = [field.wide ? "summary-textarea" : "", field.amount ? "amount-input" : "", changed ? "changed-field" : ""].filter(Boolean).join(" ");
        return (
          <label className={fieldClassName || undefined} key={field.key}>{field.label}
            {field.wide ? (
              <textarea className={controlClassName} data-journal-tab={field.tabOrder} value={editForm[field.key]}
                onChange={(event) => onChange(field.key, event.target.value)} rows={1} />
            ) : (
              <input className={controlClassName || undefined} data-journal-tab={field.tabOrder}
                inputMode={field.amount ? "numeric" : undefined}
                onFocus={field.amount ? (event) => event.currentTarget.select() : undefined}
                value={editForm[field.key]} onChange={(event) => onChange(field.key, event.target.value)} />
            )}
          </label>
        );
      })}
    </div></fieldset>
  );
}

function VoucherDateField({ value, changed, onChange }: {
  value: string;
  changed: boolean;
  onChange: (value: string) => void;
}) {
  const parts = parseVoucherDate(value);
  const yearMonth = parts
    ? `${String(parts.year).padStart(4, "0")}-${String(parts.month).padStart(2, "0")}`
    : "";
  const availableDays = parts ? getDaysInMonth(parts.year, parts.month) : 0;
  const controlClassName = changed ? "changed-field" : undefined;

  return <div className={`voucher-date-field${changed ? " field-changed" : ""}`}>
    <span className="voucher-date-title">伝票日付</span>
    <div className="voucher-date-controls">
      <label>年月
        <input type="month" className={controlClassName} data-journal-tab={1} value={yearMonth}
          onChange={(event) => onChange(changeVoucherMonth(value, event.target.value))} />
      </label>
      <label>日
        <select className={controlClassName} data-journal-tab={2} value={parts ? String(parts.day) : ""}
          onChange={(event) => onChange(changeVoucherDay(value, event.target.value))} disabled={!parts}>
          {!parts && <option value="">-</option>}
          {Array.from({ length: availableDays }, (_, index) => index + 1).map((day) => (
            <option value={day} key={day}>{day}日</option>
          ))}
        </select>
      </label>
    </div>
  </div>;
}

function AccountMasterField({ side, code, name, masters, mastersLoading, mastersError, changed, tabOrder, onChange }: {
  side: "借方" | "貸方";
  code: string;
  name: string;
  masters: JournalMastersResponse | null;
  mastersLoading: boolean;
  mastersError: string | null;
  changed: boolean;
  tabOrder: number;
  onChange: (code: string) => void;
}) {
  const currentAccount = findAccountByCode(masters, code);
  const hasCurrentValue = Boolean(code.trim() || name.trim());
  const selectValue = !hasCurrentValue ? "" : currentAccount?.selectable ? currentAccount.code : "__current_invalid__";
  const selectableAccounts = masters?.accounts.filter((account) => account.selectable) ?? [];
  const note = mastersError
    ? "マスター取得エラーのため、科目選択を利用できません。"
    : !masters || mastersLoading
      ? "マスター未取得のため科目選択は利用できません。"
      : null;

  return <div className={`account-master-field${changed ? " field-changed" : ""}`}>
    <label>科目
      <select className={`master-select${changed ? " changed-field" : ""}`} data-journal-tab={tabOrder} value={selectValue}
        title="科目コードと科目名は連動します"
        onChange={(event) => onChange(event.target.value)} disabled={!masters || mastersLoading || Boolean(mastersError)}>
        <option value="" disabled>{mastersLoading ? "マスター読み込み中…" : "科目を選択してください"}</option>
        {hasCurrentValue && (!currentAccount || !currentAccount.selectable) && <option value="__current_invalid__" disabled>
          現在値：{code || "コードなし"}　{name || "名称なし"}（通常選択対象外）
        </option>}
        {selectableAccounts.map((account) => <option value={account.code} key={account.code}>
          {account.code}　{account.name}
        </option>)}
      </select>
    </label>
    {note && <p className="master-select-note">{note}</p>}
    {masters && code.trim() && !currentAccount && <p className="unselectable-account-warning">
      現在の{side}科目 {code} {name} はマスターに存在しません。有効な科目を選び直してください。
    </p>}
    {currentAccount && !currentAccount.selectable && <p className="unselectable-account-warning">
      現在の{side}科目 {currentAccount.code} {currentAccount.name} は通常選択対象外です。有効な科目を選び直してください。
    </p>}
  </div>;
}

function DepartmentMasterField({ side, code, name, masters, mastersLoading, mastersError, changed, tabOrder, onChange }: {
  side: "借方" | "貸方";
  code: string;
  name: string;
  masters: JournalMastersResponse | null;
  mastersLoading: boolean;
  mastersError: string | null;
  changed: boolean;
  tabOrder: number;
  onChange: (code: string) => void;
}) {
  const currentDepartment = findDepartmentByCode(masters, code);
  const hasCurrentValue = Boolean(code.trim() || name.trim());
  const currentValueMatches = Boolean(currentDepartment && currentDepartment.name === name.trim());
  const selectValue = !hasCurrentValue ? "" : currentValueMatches ? currentDepartment?.code ?? "" : "__current_invalid__";
  const note = mastersError
    ? "マスター取得エラーのため、部門選択を利用できません。"
    : !masters || mastersLoading
      ? "マスター未取得のため部門選択は利用できません。"
      : null;

  return <div className={`account-master-field${changed ? " field-changed" : ""}`}>
    <label>部門
      <select className={`master-select${changed ? " changed-field" : ""}`} data-journal-tab={tabOrder} value={selectValue}
        title="部門コードと部門名は連動します"
        onChange={(event) => onChange(event.target.value)} disabled={!masters || mastersLoading || Boolean(mastersError)}>
        <option value="">部門なし</option>
        {hasCurrentValue && !currentValueMatches && <option value="__current_invalid__" disabled>
          現在値：{code || "コードなし"}　{name || "名称なし"}（マスター不一致）
        </option>}
        {masters?.departments.map((department) => <option value={department.code} key={department.code}>
          {department.label}
        </option>)}
      </select>
    </label>
    {note && <p className="master-select-note">{note}</p>}
    {masters && hasCurrentValue && !currentValueMatches && <p className="unselectable-account-warning">
      現在の{side}部門 {code || "（コードなし）"} {name || "（名称なし）"} は部門マスターと一致しません。有効な部門を選び直してください。
    </p>}
  </div>;
}

function SubAccountMasterField({ side, accountCode, code, name, masters, mastersLoading, mastersError, changed, tabOrder, onChange }: {
  side: "借方" | "貸方";
  accountCode: string;
  code: string;
  name: string;
  masters: JournalMastersResponse | null;
  mastersLoading: boolean;
  mastersError: string | null;
  changed: boolean;
  tabOrder: number;
  onChange: (code: string) => void;
}) {
  const relations = getSubAccountRelations(masters, accountCode);
  const currentRelation = findSubAccountRelation(masters, accountCode, code);
  const hasCurrentValue = Boolean(code.trim() || name.trim());
  const currentNameMatches = Boolean(currentRelation && currentRelation.sub_name === name.trim());
  const selectValue = !hasCurrentValue
    ? ""
    : currentRelation
      ? currentRelation.sub_code
      : "__current_invalid__";
  const disabled = !masters || mastersLoading || Boolean(mastersError);

  return <div className={`account-master-field${changed ? " field-changed" : ""}`}>
    <label>補助
      <select className={`master-select${changed ? " changed-field" : ""}`} data-journal-tab={tabOrder} value={selectValue}
        title="選択中の科目で使用できる補助を表示します"
        onChange={(event) => onChange(event.target.value)} disabled={disabled}>
        <option value="">補助なし</option>
        {hasCurrentValue && !currentRelation && <option value="__current_invalid__" disabled>
          現在値：{code || "コードなし"}　{name || "名称なし"}（親子関係マスター不一致）
        </option>}
        {relations.map((relation) => <option value={relation.sub_code}
          key={`${relation.account_code}-${relation.sub_code}`}>
          {relation.sub_code}　{relation.sub_name}
        </option>)}
      </select>
    </label>
    {mastersError
      ? <p className="master-select-note">マスター取得エラーのため補助選択を利用できません。</p>
      : (!masters || mastersLoading) && <p className="master-select-note">マスター読み込み中は現在値を保持します。</p>}
    {masters && hasCurrentValue && !currentRelation && <p className="unselectable-account-warning">
      現在の{side}補助 {code || "（コードなし）"} {name || "（名称なし）"} は現在の補助親子関係マスターに存在しません。補助を選び直してください。
    </p>}
    {masters && currentRelation && !currentNameMatches && <p className="unselectable-account-warning">
      現在の{side}補助名称「{name}」は旧名称です。選択肢では現在名称「{currentRelation.sub_name}」を表示し、登録準備時に更新します。
    </p>}
  </div>;
}

function BlockRowsTable({ candidate }: { candidate: JournalCandidate }) {
  return (
    <details className="block-panel">
      <summary>同一伝票ブロックを表示（{candidate.block_rows.length}件）</summary>
      {candidate.block_rows.length > 0 ? (
        <div className="table-scroll"><table><thead><tr>
          {blockRowFields.map((field) => <th key={field.key}>{field.label}</th>)}
        </tr></thead><tbody>
          {candidate.block_rows.slice(0, 5).map((row, index) => <tr key={index}>
            {blockRowFields.map((field) => <td className={field.amount ? "table-amount" : undefined} key={field.key}>
              {field.amount ? formatAmount(getString(row, field.key)) : displayValue(row[field.key])}
            </td>)}
          </tr>)}
        </tbody></table></div>
      ) : <p className="muted">同一伝票ブロック情報はありません。</p>}
    </details>
  );
}

export default function App() {
  const [activeWorkspace, setActiveWorkspace] = useState<Workspace>("journal");
  const [keyword, setKeyword] = useState("りそな銀行");
  const [department, setDepartment] = useState("");
  const [amount, setAmount] = useState("");
  const [limit, setLimit] = useState<5 | 10 | 20>(5);
  const [result, setResult] = useState<JournalSearchResponse | null>(null);
  const [selectedCandidate, setSelectedCandidate] = useState<JournalCandidate | null>(null);
  const [editForm, setEditForm] = useState<JournalEditForm | null>(null);
  const [initialEditForm, setInitialEditForm] = useState<JournalEditForm | null>(null);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [prepareLoading, setPrepareLoading] = useState(false);
  const [prepareResponse, setPrepareResponse] = useState<PrepareRegistrationResponse | null>(null);
  const [prepareError, setPrepareError] = useState<string | null>(null);
  const [prepareStatusMessage, setPrepareStatusMessage] = useState<string | null>(null);
  const [subClearWarning, setSubClearWarning] = useState<string | null>(null);
  const [registrationCart, setRegistrationCart] = useState<RegistrationCartItem[]>([]);
  const [cartStatusMessage, setCartStatusMessage] = useState<string | null>(null);
  const [epsonDownloadLoading, setEpsonDownloadLoading] = useState(false);
  const [epsonSaveLoading, setEpsonSaveLoading] = useState(false);
  const [inputExcelDownloadLoading, setInputExcelDownloadLoading] = useState(false);
  const [inputExcelSaveLoading, setInputExcelSaveLoading] = useState(false);
  const [masters, setMasters] = useState<JournalMastersResponse | null>(null);
  const [mastersLoading, setMastersLoading] = useState(false);
  const [mastersError, setMastersError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setMastersLoading(true);
    setMastersError(null);
    fetchJournalMasters()
      .then((response) => {
        if (!cancelled) setMasters(response);
      })
      .catch((caughtError) => {
        if (!cancelled) {
          setMasters(null);
          setMastersError(caughtError instanceof Error ? caughtError.message : "マスター取得中に不明なエラーが発生しました。");
        }
      })
      .finally(() => {
        if (!cancelled) setMastersLoading(false);
      });
    return () => { cancelled = true; };
  }, []);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLoading(true);
    setError(null);
    setStatusMessage(null);
    setSelectedCandidate(null);
    setEditForm(null);
    setInitialEditForm(null);
    setPrepareResponse(null);
    setPrepareError(null);
    setPrepareStatusMessage(null);
    setSubClearWarning(null);
    const request: JournalSearchRequest = {
      keyword, department: department.trim() || null, amount: amount === "" ? null : Number(amount), limit,
    };
    try {
      setResult(await searchJournals(request));
      setStatusMessage("検索結果を更新しました。候補を選択してください。");
    }
    catch (caughtError) {
      setResult(null);
      setError(caughtError instanceof Error ? caughtError.message : "検索中に不明なエラーが発生しました。");
    } finally { setLoading(false); }
  }

  function handleCandidateSelect(candidate: JournalCandidate) {
    setSelectedCandidate(candidate);
    setStatusMessage(null);
    setPrepareResponse(null);
    setPrepareError(null);
    setPrepareStatusMessage(null);
    setSubClearWarning(null);
    const firstEditableRow = candidate.editable_rows[0];
    const nextEditForm = firstEditableRow ? buildEditFormFromRow(firstEditableRow) : null;
    setEditForm(nextEditForm ? { ...nextEditForm } : null);
    setInitialEditForm(nextEditForm ? { ...nextEditForm } : null);
  }

  function updateEditForm(key: keyof JournalEditForm, value: string) {
    setEditForm((current) => current ? { ...current, [key]: value } : current);
    if (["debitSubCode", "debitSubName", "creditSubCode", "creditSubName"].includes(key)) {
      setSubClearWarning(null);
    }
    setPrepareResponse(null);
    setPrepareError(null);
    setPrepareStatusMessage(null);
  }

  function updateAccountSelection(side: "debit" | "credit", code: string) {
    const account = findAccountByCode(masters, code);
    if (!account?.selectable || !editForm) return;

    const currentAccountCode = side === "debit" ? editForm.debitAccountCode : editForm.creditAccountCode;
    if (currentAccountCode === account.code) return;

    const hadSubAccount = side === "debit"
      ? Boolean(editForm.debitSubCode.trim() || editForm.debitSubName.trim())
      : Boolean(editForm.creditSubCode.trim() || editForm.creditSubName.trim());
    setEditForm(side === "debit"
      ? {
        ...editForm,
        debitAccountCode: account.code,
        debitAccountName: account.name,
        debitSubCode: "",
        debitSubName: "",
      }
      : {
        ...editForm,
        creditAccountCode: account.code,
        creditAccountName: account.name,
        creditSubCode: "",
        creditSubName: "",
      });
    setSubClearWarning(hadSubAccount ? `${side === "debit" ? "借方" : "貸方"}科目を変更したため、${side === "debit" ? "借方" : "貸方"}補助をクリアしました。` : null);
    setPrepareResponse(null);
    setPrepareError(null);
    setPrepareStatusMessage(null);
  }

  function updateDepartmentSelection(side: "debit" | "credit", code: string) {
    if (!editForm) return;
    const department = code ? findDepartmentByCode(masters, code) : undefined;
    if (code && !department) return;

    setEditForm(changeDepartmentSelection(editForm, side, department));
    setPrepareResponse(null);
    setPrepareError(null);
    setPrepareStatusMessage(null);
  }

  function updateSubAccountSelection(side: "debit" | "credit", code: string) {
    if (!editForm) return;
    const accountCode = side === "debit" ? editForm.debitAccountCode : editForm.creditAccountCode;
    const relation = code ? findSubAccountRelation(masters, accountCode, code) : undefined;
    if (code && !relation) return;

    setEditForm(changeSubAccountSelection(editForm, side, relation));
    setSubClearWarning(null);
    setPrepareResponse(null);
    setPrepareError(null);
    setPrepareStatusMessage(null);
  }

  function resetEditForm() {
    if (initialEditForm) {
      setEditForm({ ...initialEditForm });
      setPrepareResponse(null);
      setPrepareError(null);
      setPrepareStatusMessage(null);
      setSubClearWarning(null);
    }
  }

  async function handlePrepareRegistration() {
    if (!selectedCandidate || !editForm) return;
    const sourceRow = selectedCandidate.editable_rows[0];
    if (selectedCandidate.editable_rows.length !== 1 || !sourceRow) {
      setPrepareStatusMessage("編集対象が通常1行仕訳ではないため登録準備できません。");
      return;
    }
    const currentMasterChecks = checkEditFormMasters(editForm, masters);
    if (!masters || mastersError || currentMasterChecks.some((message) => message.level === "error")) {
      setPrepareStatusMessage("マスター照合エラーを解消してから登録準備を実行してください。");
      return;
    }

    const nullable = (value: string) => value.trim() || null;
    const request: PrepareRegistrationRequest = {
      edit_form: {
        voucher_date: editForm.voucherDate,
        voucher_no: nullable(editForm.voucherNo),
        voucher_summary: nullable(editForm.voucherSummary),
        debit_account_code: editForm.debitAccountCode,
        debit_account_name: nullable(editForm.debitAccountName),
        debit_sub_code: nullable(editForm.debitSubCode),
        debit_sub_name: nullable(editForm.debitSubName),
        debit_dept_code: nullable(editForm.debitDeptCode),
        debit_dept_name: nullable(editForm.debitDeptName),
        credit_account_code: editForm.creditAccountCode,
        credit_account_name: nullable(editForm.creditAccountName),
        credit_sub_code: nullable(editForm.creditSubCode),
        credit_sub_name: nullable(editForm.creditSubName),
        credit_dept_code: nullable(editForm.creditDeptCode),
        credit_dept_name: nullable(editForm.creditDeptName),
        amount: editForm.amount,
        summary: nullable(editForm.summary),
        source_debit_amount: nullable(editForm.debitAmount),
        source_credit_amount: nullable(editForm.creditAmount),
      },
      candidate_meta: {
        rank: selectedCandidate.rank,
        score: selectedCandidate.score,
        pattern_key: selectedCandidate.pattern_key,
        pattern_rank: selectedCandidate.pattern_rank,
        editable_row_count: selectedCandidate.editable_rows.length,
        source_row_count: selectedCandidate.source_rows.length,
        block_row_count: selectedCandidate.block_rows.length,
        has_fukugo: selectedCandidate.has_fukugo,
        has_sundry: selectedCandidate.has_sundry,
        contains_fukugo_or_sundry: selectedCandidate.contains_fukugo_or_sundry,
        show_block_rows: selectedCandidate.show_block_rows,
        is_complex: selectedCandidate.is_complex,
      },
      source_row: sourceRow,
    };

    setPrepareLoading(true);
    setPrepareError(null);
    setPrepareStatusMessage(null);
    try {
      const response = await prepareRegistration(request);
      setPrepareResponse(response);
      if (
        response.ok
        && response.registration_id
        && response.prepared_journal
        && response.epson_preview_row
        && response.epson_base_row
        && response.print_metadata
        && response.print_warnings
      ) {
        const cartItem: RegistrationCartItem = {
          ...response,
          registration_id: response.registration_id,
          prepared_journal: response.prepared_journal,
          epson_preview_row: response.epson_preview_row,
          epson_base_row: response.epson_base_row,
          print_metadata: response.print_metadata,
          print_warnings: response.print_warnings,
          addedAt: new Date().toISOString(),
        };
        if (registrationCart.some((item) => item.registration_id === response.registration_id)) {
          setPrepareStatusMessage("同じ内容の登録予定仕訳が既にカートにあります。");
        } else {
          setPrepareStatusMessage("登録予定へ追加しました（画面上の確認のみ）。");
          setCartStatusMessage("登録予定仕訳をカートへ追加しました。");
          setRegistrationCart((current) => current.some((item) => item.registration_id === response.registration_id)
            ? current : [...current, cartItem]);
        }
      }
    } catch (caughtError) {
      setPrepareResponse(null);
      setPrepareError(caughtError instanceof Error ? caughtError.message : "登録準備中に不明なエラーが発生しました。");
    } finally {
      setPrepareLoading(false);
    }
  }

  function removeCartItem(registrationId: string) {
    setRegistrationCart((current) => current.filter((item) => item.registration_id !== registrationId));
    setCartStatusMessage("登録予定仕訳をカートから削除しました。");
  }

  function clearRegistrationCart() {
    setRegistrationCart([]);
    setCartStatusMessage("画面上の出力待ちカートを空にしました。");
  }

  async function handleEpsonCsvDownload() {
    if (registrationCart.length === 0 || epsonDownloadLoading) return;

    setEpsonDownloadLoading(true);
    setCartStatusMessage(null);
    try {
      const downloaded = await downloadEpsonCsv({
        items: registrationCart.map((item) => ({
          registration_id: item.registration_id,
          prepared_journal: item.prepared_journal,
          epson_base_row: item.epson_base_row,
        })),
      });
      const objectUrl = URL.createObjectURL(downloaded.blob);
      try {
        const link = document.createElement("a");
        link.href = objectUrl;
        link.download = downloaded.filename;
        document.body.appendChild(link);
        link.click();
        link.remove();
      } finally {
        URL.revokeObjectURL(objectUrl);
      }
      setCartStatusMessage("EPSON CSVをダウンロードしました。検索DBには登録していません。");
    } catch (caughtError) {
      setCartStatusMessage(caughtError instanceof Error ? caughtError.message : "EPSON CSVをダウンロードできませんでした。");
    } finally {
      setEpsonDownloadLoading(false);
    }
  }

  async function handleEpsonCsvSave() {
    if (registrationCart.length === 0 || epsonSaveLoading) return;

    setEpsonSaveLoading(true);
    setCartStatusMessage(null);
    try {
      const response = await saveEpsonCsv({
        items: registrationCart.map((item) => ({
          registration_id: item.registration_id,
          prepared_journal: item.prepared_journal,
          epson_base_row: item.epson_base_row,
        })),
      });
      setCartStatusMessage(`${response.message} 保存先：${response.save_path}`);
    } catch (caughtError) {
      setCartStatusMessage(caughtError instanceof Error ? caughtError.message : "EPSON CSVを正式保存できませんでした。検索DBは更新していません。");
    } finally {
      setEpsonSaveLoading(false);
    }
  }

  async function handleInputExcelDownload() {
    if (registrationCart.length === 0 || inputExcelDownloadLoading) return;

    setInputExcelDownloadLoading(true);
    setCartStatusMessage(null);
    try {
      const downloaded = await downloadInputExcel({
        items: registrationCart.map((item) => ({
          registration_id: item.registration_id,
          prepared_journal: item.prepared_journal,
          epson_base_row: item.epson_base_row,
          print_metadata: item.print_metadata,
          print_warnings: item.print_warnings,
        })),
      });
      const objectUrl = URL.createObjectURL(downloaded.blob);
      try {
        const link = document.createElement("a");
        link.href = objectUrl;
        link.download = downloaded.filename;
        document.body.appendChild(link);
        link.click();
        link.remove();
      } finally {
        URL.revokeObjectURL(objectUrl);
      }
      setCartStatusMessage("入力用Excelをダウンロードしました。検索DBは更新していません。");
    } catch (caughtError) {
      setCartStatusMessage(caughtError instanceof Error ? caughtError.message : "入力用Excelをダウンロードできませんでした。");
    } finally {
      setInputExcelDownloadLoading(false);
    }
  }

  async function handleInputExcelSave() {
    if (registrationCart.length === 0 || inputExcelSaveLoading) return;

    setInputExcelSaveLoading(true);
    setCartStatusMessage(null);
    try {
      const response = await saveInputExcel({
        items: registrationCart.map((item) => ({
          registration_id: item.registration_id,
          prepared_journal: item.prepared_journal,
          epson_base_row: item.epson_base_row,
          print_metadata: item.print_metadata,
          print_warnings: item.print_warnings,
        })),
      });
      setCartStatusMessage(`${response.message} 保存先：${response.saved_path}`);
    } catch (caughtError) {
      setCartStatusMessage(caughtError instanceof Error ? caughtError.message : "入力用Excelを保存できませんでした。検索DBは更新していません。");
    } finally {
      setInputExcelSaveLoading(false);
    }
  }

  const selectedCandidateIsComplex = Boolean(selectedCandidate && (
    selectedCandidate.has_fukugo || selectedCandidate.has_sundry || selectedCandidate.contains_fukugo_or_sundry ||
    selectedCandidate.show_block_rows || selectedCandidate.is_complex
  ));
  const sourceAmountsEqual = areSourceAmountsEqual(editForm);
  const editFormChanged = isEditFormChanged(editForm, initialEditForm);
  const selectedSummary = selectedCandidate ? getCandidateSummary(selectedCandidate) : null;
  const cartTotalAmount = registrationCart.reduce((total, item) => total + getCartAmount(item), 0);
  const masterCheckMessages = checkEditFormMasters(editForm, masters);
  const masterCheckCounts = {
    ok: masterCheckMessages.filter((message) => message.level === "ok").length,
    warning: masterCheckMessages.filter((message) => message.level === "warning").length,
    error: masterCheckMessages.filter((message) => message.level === "error").length,
  };
  const hasMasterErrors = masterCheckCounts.error > 0;

  if (activeWorkspace === "receivable") {
    return (
      <main className="app-shell receivable-shell">
        <header className="page-header">
          <div className="page-title"><h1>journal-ai</h1><span>未収消込</span></div>
          <WorkspaceTabs active={activeWorkspace} onChange={setActiveWorkspace} />
          <div className="page-header-meta">
            {masters?.system && <span className="fiscal-year-status">
              会計年度：{masters.system.current_fiscal_year}年度
              （{masters.system.fiscal_year_start_month}月～{masters.system.fiscal_year_end_month}月）
            </span>}
            <span className="workspace-status">Preview・未確定</span>
          </div>
        </header>
        <ReceivableWorkspace masters={masters} mastersLoading={mastersLoading} mastersError={mastersError} />
      </main>
    );
  }

  return (
    <main className="app-shell" onKeyDown={handleAppTabKeyDown}>
      <header className="page-header">
        <div className="page-title"><h1>journal-ai</h1><span>通常仕訳</span></div>
        <WorkspaceTabs active={activeWorkspace} onChange={setActiveWorkspace} />
        <div className="page-header-meta">
          {masters?.system && <span className="fiscal-year-status">
            会計年度：{masters.system.current_fiscal_year}年度
            （{masters.system.fiscal_year_start_month}月～{masters.system.fiscal_year_end_month}月）
          </span>}
          <span className="workspace-status">確認用・未登録</span>
        </div>
      </header>

      <div className="split-layout">
        <section className="pane left-pane" aria-label="検索と候補一覧">
          <div className="search-panel">
            <div className="pane-heading"><h2>検索条件</h2>
              {result && <span className="result-pill">{result.count}件</span>}
            </div>
            <form className="search-form" onSubmit={handleSubmit}>
              <label className="search-keyword">キーワード<input data-search-tab={1} value={keyword}
                onChange={(event) => setKeyword(event.target.value)} required /></label>
              <label className="search-department">部門<input data-search-tab={2} value={department}
                onChange={(event) => setDepartment(event.target.value)} placeholder="空欄可" /></label>
              <label className="search-amount">金額<input type="number" data-search-tab={3} value={amount}
                onChange={(event) => setAmount(event.target.value)} placeholder="空欄可" /></label>
              <label className="search-limit">表示件数<select data-search-tab={4} value={limit}
                onChange={(event) => setLimit(Number(event.target.value) as 5 | 10 | 20)}>
                <option value={5}>5</option><option value={10}>10</option><option value={20}>20</option>
              </select></label>
              <button type="submit" data-search-tab={5} disabled={loading}>{loading ? "検索中…" : "検索"}</button>
            </form>
            <details className={`master-status ${mastersError ? "error" : masters ? "ready" : "loading"}`}
              aria-live="polite" open={Boolean(mastersError)}>
              <summary>{mastersLoading ? <strong>マスター：読み込み中…</strong> : mastersError ? (
                <strong>マスター取得エラー：{mastersError}</strong>
              ) : masters ? (
                <strong>マスター：科目 {masters.accounts.length}件 / 補助 {masters.sub_accounts.length}件 / 部門 {masters.departments.length}件</strong>
              ) : <strong>マスター：読み込み準備中…</strong>}</summary>
              {masters && <div className="master-status-details">
                <span>選択可能科目 {masters.diagnostics.selectable_account_count}件 / 選択不可科目 {masters.diagnostics.unselectable_account_count}件</span>
                <details className="master-diagnostics">
                  <summary>マスター診断を表示</summary>
                  <pre>{JSON.stringify(masters.diagnostics, null, 2)}</pre>
                </details>
              </div>}
            </details>
            {error && <p className="error-message">{error}</p>}
            {statusMessage && <p className="status-message">{statusMessage}</p>}
          </div>

          <div className="candidate-panel" aria-live="polite">
            <div className="pane-heading candidate-list-heading"><h2>候補一覧</h2>
              {result && <span className="muted">検索結果: {result.count}件</span>}
            </div>
            {result ? <div className="candidate-list">
              {result.candidates.map((candidate) => <CandidateCard
                key={`${candidate.rank}-${candidate.pattern_key.join("-")}`} candidate={candidate}
                selected={selectedCandidate === candidate} onSelect={handleCandidateSelect}
              />)}
            </div> : <p className="candidate-placeholder">検索すると、ここに候補が表示されます。</p>}
          </div>
        </section>

        <section className="pane right-pane" aria-live="polite">
          <div className="edit-panel-heading"><h2>仕訳編集</h2>
            <span className="status-badge">確認用・未登録</span>
          </div>
          {!selectedCandidate && <div className="empty-edit-state">
            <div className="empty-state-icon" aria-hidden="true">↖</div><h3>候補を選択してください</h3>
            <p>左側の候補を選択すると、editable_rows[0] の内容を編集フォームに表示します。<br />
              この画面ではまだ登録・CSV出力は行いません。</p>
          </div>}
          {selectedCandidate && <>
            <div className="edit-overview">
              <div className="status-strip">
                <div className="status-strip-main">
                  <strong>候補{selectedCandidate.rank} / Score {selectedCandidate.score}</strong>
                  {selectedSummary && <>
                    <span className="overview-journal">{selectedSummary.debit} <b aria-hidden="true">→</b> {selectedSummary.credit}</span>
                    <b className="overview-amount">{selectedSummary.amount}</b>
                  </>}
                  <span className="unregistered-badge">確認用・未登録</span>
                </div>
                <div className="status-strip-secondary">
                  {selectedSummary && <span title={selectedSummary.summary}>{selectedSummary.summary}</span>}
                  <details className="selection-technical-details"><summary>技術情報</summary>
                    <small>パターンキー: {selectedCandidate.pattern_key.join(" / ") || "-"}</small>
                  </details>
                </div>
              </div>
              <div className={`edit-status ${editFormChanged ? "changed" : "unchanged"}`}>
                <strong title="変更内容は保存されません。">{editFormChanged ? "変更あり（未保存）" : "未変更"}</strong>
              </div>
              {editForm && <details className="master-check-panel" aria-live="polite"
                open={!masters || masterCheckCounts.warning > 0 || masterCheckCounts.error > 0}>
                <summary className="master-check-heading">
                  <h3>マスター</h3>
                  <div className="master-check-summary">
                    <span className="ok">OK {masterCheckCounts.ok}</span>
                    <span className="warning">警告 {masterCheckCounts.warning}</span>
                    <span className="error">エラー {masterCheckCounts.error}</span>
                  </div>
                </summary>
                {!masters ? <p className={mastersError ? "master-check-unavailable error" : "master-check-unavailable"}>
                  {mastersError ? "マスターを取得できないため照合できません。" : "マスターを読み込んでいます。"}
                </p> : <ul className="master-check-list">
                  {masterCheckMessages.map((message, index) => <li className={message.level} key={`${message.level}-${message.message}-${index}`}>
                    <span aria-hidden="true">{message.level === "ok" ? "✓" : message.level === "warning" ? "!" : "×"}</span>
                    {message.message}
                  </li>)}
                </ul>}
              </details>}
            </div>
            {subClearWarning && <p className="notice notice-warning" role="status">{subClearWarning}</p>}
            {selectedCandidate.editable_rows.length === 0 && <p className="notice notice-error">この候補には編集用行がありません。</p>}
            {selectedCandidate.editable_rows.length > 1 && <p className="notice notice-warning">この候補は複数行の編集候補です。今回は通常1行仕訳向けの確認として先頭行のみ表示しています。</p>}
            {selectedCandidateIsComplex && <p className="notice notice-warning">
              この候補は資金複合または諸口を含む可能性があります。block_rows を確認し、登録時は実際の相手科目へ修正してください。
            </p>}
            {sourceAmountsEqual === false && <p className="notice notice-warning">
              元データの借方金額と貸方金額が一致していません。内容を確認してください。
            </p>}
            {editForm && <form className="edit-form" onSubmit={(event) => event.preventDefault()}>
              <div className="journal-entry-grid">
                <FormSection title="共通情報" fields={commonFields} editForm={editForm} initialEditForm={initialEditForm} onChange={updateEditForm}
                  className="common-section" gridClassName="common-info-grid">
                  <VoucherDateField value={editForm.voucherDate}
                    changed={isFieldChanged("voucherDate", editForm, initialEditForm)}
                    onChange={(value) => updateEditForm("voucherDate", value)} />
                </FormSection>
                <FormSection title="借方" fields={debitFields} editForm={editForm} initialEditForm={initialEditForm} onChange={updateEditForm}
                  className="side-section debit" gridClassName="side-form-grid">
                  <AccountMasterField side="借方" code={editForm.debitAccountCode} name={editForm.debitAccountName}
                    masters={masters} mastersLoading={mastersLoading} mastersError={mastersError}
                    changed={isFieldChanged("debitAccountCode", editForm, initialEditForm) || isFieldChanged("debitAccountName", editForm, initialEditForm)}
                    tabOrder={7}
                    onChange={(code) => updateAccountSelection("debit", code)} />
                  <SubAccountMasterField side="借方" accountCode={editForm.debitAccountCode}
                    code={editForm.debitSubCode} name={editForm.debitSubName}
                    masters={masters} mastersLoading={mastersLoading} mastersError={mastersError}
                    changed={isFieldChanged("debitSubCode", editForm, initialEditForm) || isFieldChanged("debitSubName", editForm, initialEditForm)}
                    tabOrder={8}
                    onChange={(code) => updateSubAccountSelection("debit", code)} />
                  <DepartmentMasterField side="借方" code={editForm.debitDeptCode} name={editForm.debitDeptName}
                    masters={masters} mastersLoading={mastersLoading} mastersError={mastersError}
                    changed={isFieldChanged("debitDeptCode", editForm, initialEditForm) || isFieldChanged("debitDeptName", editForm, initialEditForm)}
                    tabOrder={9}
                    onChange={(code) => updateDepartmentSelection("debit", code)} />
                </FormSection>
                <FormSection title="貸方" fields={creditFields} editForm={editForm} initialEditForm={initialEditForm} onChange={updateEditForm}
                  className="side-section credit" gridClassName="side-form-grid">
                  <AccountMasterField side="貸方" code={editForm.creditAccountCode} name={editForm.creditAccountName}
                    masters={masters} mastersLoading={mastersLoading} mastersError={mastersError}
                    changed={isFieldChanged("creditAccountCode", editForm, initialEditForm) || isFieldChanged("creditAccountName", editForm, initialEditForm)}
                    tabOrder={10}
                    onChange={(code) => updateAccountSelection("credit", code)} />
                  <SubAccountMasterField side="貸方" accountCode={editForm.creditAccountCode}
                    code={editForm.creditSubCode} name={editForm.creditSubName}
                    masters={masters} mastersLoading={mastersLoading} mastersError={mastersError}
                    changed={isFieldChanged("creditSubCode", editForm, initialEditForm) || isFieldChanged("creditSubName", editForm, initialEditForm)}
                    tabOrder={11}
                    onChange={(code) => updateSubAccountSelection("credit", code)} />
                  <DepartmentMasterField side="貸方" code={editForm.creditDeptCode} name={editForm.creditDeptName}
                    masters={masters} mastersLoading={mastersLoading} mastersError={mastersError}
                    changed={isFieldChanged("creditDeptCode", editForm, initialEditForm) || isFieldChanged("creditDeptName", editForm, initialEditForm)}
                    tabOrder={12}
                    onChange={(code) => updateDepartmentSelection("credit", code)} />
                </FormSection>
              </div>
              <details className="amount-summary-panel" open={sourceAmountsEqual === false}>
                <summary>
                  <strong>{editForm.amount ? `金額 ${formatAmountWithUnit(editForm.amount)}` : "金額 未入力"}</strong>
                  <span className={`source-amount-check ${sourceAmountsEqual === true ? "match" : sourceAmountsEqual === false ? "mismatch" : "incomplete"}`}>
                    {sourceAmountsEqual === true ? "✓ 借貸同額" : sourceAmountsEqual === false ? "! 借貸不一致" : "借貸金額を確認"}
                  </span>
                </summary>
                <div className="amount-source-details">
                  <span>出力想定：借方金額・貸方金額へ同額反映</span>
                  <small>元データ：借方 {formatAmountWithUnit(editForm.debitAmount)} / 貸方 {formatAmountWithUnit(editForm.creditAmount)}</small>
                </div>
              </details>
              <div className="edit-form-footer">
                <div className="prepare-guidance">
                  <p title="登録予定データをAPIで整形するだけで、保存・DB登録・CSV出力は行いません。">登録準備のみ（保存・出力なし）</p>
                  {hasMasterErrors && <strong>マスター不一致があります。有効なマスター値へ修正してください。</strong>}
                </div>
                <div className="edit-actions">
                  <button type="button" className="reset-button" onClick={resetEditForm} disabled={!editFormChanged}>候補選択時の値に戻す</button>
                  <button type="button" className="prepare-action" data-journal-tab={13} onClick={handlePrepareRegistration}
                    disabled={prepareLoading || mastersLoading || !masters || Boolean(mastersError) || hasMasterErrors}>
                    {prepareLoading ? "登録準備中…" : "登録予定へ追加（確認のみ）"}
                  </button>
                </div>
              </div>
              {prepareError && <p className="prepare-result error-message">{prepareError}</p>}
              {prepareStatusMessage && <p className="prepare-result status-message">{prepareStatusMessage}</p>}
              {prepareResponse?.blocked && <div className="prepare-result prepare-blocked" role="alert">
                <strong>登録準備APIの検証でブロックされました</strong>
                <ul>{prepareResponse.errors.map((reason, index) => <li key={`${reason}-${index}`}>理由: {reason}</li>)}</ul>
                {prepareResponse.warnings.map((warning, index) => <p key={`${warning}-${index}`}>注意: {warning}</p>)}
              </div>}
              {prepareResponse?.ok && prepareResponse.warnings.length > 0 && <div className="prepare-result prepare-warning">
                {prepareResponse.warnings.map((warning, index) => <p key={`${warning}-${index}`}>{warning}</p>)}
              </div>}
            </form>}
            <BlockRowsTable candidate={selectedCandidate} />
          </>}
        </section>
      </div>

      <details className="registration-panel" aria-live="polite">
        <summary className="cart-bar" data-cart-tab="">
          <strong>出力待ち</strong>
          <span>{registrationCart.length}件</span>
          <b>{formatAmountNumber(cartTotalAmount)}</b>
          {cartStatusMessage && <small>{cartStatusMessage}</small>}
          <em aria-hidden="true" />
        </summary>
        <div className="cart-details">
          <div className="cart-panel-heading">
            <p className="registration-panel-note">画面上の一時保持です。リロードすると消え、CSVダウンロードしても検索DBへは保存されません。</p>
            <div className="cart-actions">
              <button type="button" className="epson-download-button" data-cart-tab="" onClick={handleEpsonCsvDownload}
                disabled={registrationCart.length === 0 || epsonDownloadLoading}>
                {epsonDownloadLoading ? "ダウンロード準備中…" : "EPSON CSVダウンロード"}
              </button>
              <button type="button" className="epson-save-button" data-cart-tab="" onClick={handleEpsonCsvSave}
                disabled={registrationCart.length === 0 || epsonSaveLoading}>
                {epsonSaveLoading ? "保存・DB登録中…" : "保存先へ保存"}
              </button>
              <button type="button" className="epson-download-button" data-cart-tab="" onClick={handleInputExcelDownload}
                disabled={registrationCart.length === 0 || inputExcelDownloadLoading}>
                {inputExcelDownloadLoading ? "Excel準備中…" : "入力用Excelダウンロード"}
              </button>
              <button type="button" className="epson-save-button" data-cart-tab="" onClick={handleInputExcelSave}
                disabled={registrationCart.length === 0 || inputExcelSaveLoading}>
                {inputExcelSaveLoading ? "Excel保存中…" : "入力用Excel保存"}
              </button>
              <button type="button" className="clear-cart-button" onClick={clearRegistrationCart} disabled={registrationCart.length === 0}>カートを空にする</button>
            </div>
          </div>
          <p className="cart-save-note">保存先へ保存すると検索DBへ登録します。</p>
          <p className="cart-save-note">入力用Excelは簡易仕訳帳・印刷用です。保存・ダウンロードしても検索DBには登録しません。</p>
          <p className="cart-total-note">合計金額は画面表示用の単純合計であり、会計ロジックではありません。</p>
          {registrationCart.length === 0 ? <p className="cart-empty">登録予定はまだありません。</p> : <div className="cart-list">
            {registrationCart.map((item, index) => <article className="cart-item" key={item.registration_id}>
              <div className="cart-item-header">
                <div><span className="cart-number">No. {index + 1}</span><span className="cart-id" title={item.registration_id}>ID {shortId(item.registration_id)}</span></div>
                <button type="button" className="remove-cart-button" onClick={() => removeCartItem(item.registration_id)}>カートから削除</button>
              </div>
              <div className="cart-item-main">
                <div><span>伝票日付</span><strong>{item.prepared_journal.voucher_date || "-"}</strong></div>
                <div className="cart-journal"><span>仕訳</span><strong>
                  {item.prepared_journal.debit_account_name || item.prepared_journal.debit_account_code || "-"}
                  <b aria-hidden="true">→</b>
                  {item.prepared_journal.credit_account_name || item.prepared_journal.credit_account_code || "-"}
                </strong></div>
                <div className="cart-item-amount"><span>金額</span><strong>{formatAmountNumber(getCartAmount(item))}</strong></div>
                <div className="cart-item-summary"><span>摘要</span><p>{item.prepared_journal.summary || "-"}</p></div>
              </div>
              <details className="epson-preview"><summary>エプソンCSVプレビューを表示</summary>
                <div className="preview-table-wrap"><table className="preview-table"><tbody>
                  {epsonPreviewFields.map((field) => <tr key={field}><th>{field}</th><td>{getPreviewValue(item.epson_preview_row, field)}</td></tr>)}
                </tbody></table></div>
              </details>
            </article>)}
          </div>}
          <p className="clear-cart-note">画面上のカートだけを空にします。保存済みデータはありません。</p>
        </div>
      </details>

      <details className="dev-panel">
        <summary className="dev-panel-heading">開発情報</summary>
        <div className="dev-panel-content">
          <details><summary>APIレスポンスJSONを表示</summary><pre>{JSON.stringify(result, null, 2)}</pre></details>
          <details><summary>編集フォームstateを表示</summary><pre>{JSON.stringify(editForm, null, 2)}</pre></details>
          <details><summary>登録準備APIレスポンスを表示</summary><pre>{JSON.stringify(prepareResponse, null, 2)}</pre></details>
          <details><summary>出力待ちカートJSONを表示</summary><pre>{JSON.stringify(registrationCart, null, 2)}</pre></details>
        </div>
      </details>
    </main>
  );
}
