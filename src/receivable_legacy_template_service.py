"""Pure extraction of the legacy Streamlit receivable template selection."""
import re
import unicodedata
from datetime import datetime
from columns import EPSON_COLUMNS, COL_DATE, COL_SUMMARY, COL_DEBIT, COL_CREDIT, COL_DEBIT_SUB, COL_CREDIT_SUB
from engine import tokenize

def build_empty_epson_template_row():

    return {
        column: ""
        for column in EPSON_COLUMNS
    }


def normalize_receivable_template_text(value):

    text = unicodedata.normalize("NFKC", str(value or ""))
    text = text.lower()

    for pattern in [
        "株式会社",
        "有限会社",
        "(株)",
        "（株）",
        "株)",
        "(有)",
        "（有）",
        "㈱",
        "㈲",
    ]:
        text = text.replace(pattern.lower(), "")

    text = re.sub(r"\d+\s*月分?", "", text)
    text = re.sub(r"[0-9０-９]+号車", "", text)
    text = re.sub(r"(総務課|経理課|御中|様)$", "", text)
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"[　\s・･.,，．、。()\[\]（）【】\-ー－]", "", text)

    return text


def is_receivable_text_close(left, right):

    left = normalize_receivable_template_text(left)
    right = normalize_receivable_template_text(right)

    if not left or not right:
        return False

    if left in right or right in left:
        return True

    min_length = min(len(left), len(right))

    for size in range(min(4, min_length), 1, -1):
        for index in range(0, len(left) - size + 1):
            if left[index:index + size] in right:
                return True

    return False


def is_receivable_account_name(account):

    account = str(account or "")

    return (
        account in {"未収運賃", "未収金", "売掛金"}
        or "未収" in account
        or "売掛" in account
    )


def is_cash_account_name(account):

    return str(account or "") in {
        "普通預金",
        "当座預金",
        "現金",
    }


def get_receivable_template_quality(row):

    quality_columns = [
        "形式",
        "作成方法",
        "借方部門",
        "借方部門名",
        "借方消費税コード",
        "借方消費税業種",
        "借方消費税税率",
        "借方資金区分",
        "借方補助",
        "借方補助科目名",
        "貸方部門",
        "貸方部門名",
        "貸方消費税コード",
        "貸方消費税業種",
        "貸方消費税税率",
        "貸方資金区分",
        "貸方補助",
        "貸方補助科目名",
        "伝票摘要",
        COL_SUMMARY,
        "入力アプリ",
    ]

    return sum(
        1
        for column in quality_columns
        if str(row.get(column, "") or "").strip()
    )


def parse_receivable_template_date(row):

    value = str(row.get(COL_DATE, "") or "").strip()

    for date_format in ("%Y%m%d", "%Y/%m/%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, date_format)
        except ValueError:
            continue

    return datetime.min


def is_receivable_sub_template_match(journal_sub, row_sub):

    journal_sub = str(journal_sub or "").strip()
    row_sub = str(row_sub or "").strip()

    return journal_sub == row_sub


def is_receivable_summary_template_match(journal_summary, row):

    journal_summary = str(journal_summary or "").strip()

    if not journal_summary:
        return True

    row_text = " ".join([
        str(row.get(COL_SUMMARY, "") or ""),
        str(row.get("伝票摘要", "") or ""),
    ])

    if is_receivable_text_close(journal_summary, row_text):
        return True

    journal_tokens = set(tokenize(journal_summary))
    row_tokens = set(tokenize(row_text))

    return bool(journal_tokens & row_tokens)


def is_receivable_template_text_match(search_terms, row):

    row_text = " ".join([
        str(row.get(COL_SUMMARY, "") or ""),
        str(row.get("伝票摘要", "") or ""),
        str(row.get(COL_DEBIT_SUB, "") or ""),
        str(row.get(COL_CREDIT_SUB, "") or ""),
    ])
    row_tokens = set(tokenize(row_text))

    for term in search_terms:
        term = str(term or "").strip()

        if not term:
            continue

        if is_receivable_text_close(term, row_text):
            return True

        term_tokens = set(tokenize(term))
        if term_tokens & row_tokens:
            return True

    return False


def find_receivable_template_match(
    journal,
    customer_name="",
    source_candidates=None, records=()
):

    debit_account = str(journal.get("借方科目", "") or "").strip()
    credit_account = str(journal.get("貸方科目", "") or "").strip()
    debit_sub = str(journal.get("借方補助", "") or "").strip()
    credit_sub = str(journal.get("貸方補助", "") or "").strip()
    summary = str(journal.get("摘要", "") or "").strip()
    source_candidates = source_candidates or []
    search_terms = [
        customer_name,
        summary,
    ]

    for candidate in source_candidates:
        if not isinstance(candidate, dict):
            continue

        for column in ["取引先", "得意先名", "摘要", "未収補助"]:
            value = str(candidate.get(column, "") or "").strip()
            if value:
                search_terms.append(value)

    diagnostic = {
        "DB雛形": "なし",
        "理由": "",
        "採用理由": "",
        "借方科目": debit_account,
        "貸方科目": credit_account,
        "借方補助": debit_sub,
        "貸方補助": credit_sub,
        "取引先": str(customer_name or "").strip(),
        "摘要": summary,
        "科目一致": 0,
        "借方補助一致": 0,
        "貸方補助一致": 0,
        "補助一致": "なし",
        "摘要一致": 0,
        "摘要一致有無": "なし",
        "候補数": 0,
        "採用スコア": 0,
        "雛形日付": "",
        "雛形摘要": "",
        "雛形伝票摘要": "",
        "雛形形式": "",
        "雛形借方部門": "",
        "雛形貸方部門": "",
        "雛形借方税コード": "",
        "雛形貸方税コード": "",
        "雛形品質": 0,
    }

    if not debit_account or not credit_account:
        diagnostic["理由"] = "生成仕訳の借方科目または貸方科目が空欄です"
        return None, diagnostic

    best_row = None
    best_rank = None
    best_score = 0
    best_debit_sub_matches = False
    best_credit_sub_matches = False
    best_text_matches = False

    for rec in records:
        for row in rec.get("rows", []):

            if (
                str(row.get(COL_DEBIT, "") or "").strip()
                != debit_account
                or str(row.get(COL_CREDIT, "") or "").strip()
                != credit_account
            ):
                continue

            diagnostic["科目一致"] += 1

            debit_sub_matches = (
                bool(debit_sub)
                and is_receivable_sub_template_match(
                    debit_sub,
                    row.get(COL_DEBIT_SUB, "")
                )
            )

            if debit_sub_matches:
                diagnostic["借方補助一致"] += 1

            credit_sub_matches = (
                bool(credit_sub)
                and is_receivable_sub_template_match(
                    credit_sub,
                    row.get(COL_CREDIT_SUB, "")
                )
            )

            if credit_sub_matches:
                diagnostic["貸方補助一致"] += 1

            text_matches = is_receivable_template_text_match(
                search_terms,
                row
            )

            if not text_matches:
                continue

            diagnostic["摘要一致"] += 1
            diagnostic["候補数"] += 1
            quality_score = get_receivable_template_quality(row)
            score = (
                quality_score * 10
                + (30 if text_matches else 0)
                + (10 if debit_sub_matches else 0)
                + (10 if credit_sub_matches else 0)
            )
            rank = (
                -quality_score,
                -score,
                -parse_receivable_template_date(row).toordinal(),
            )

            if best_rank is None or rank < best_rank:
                best_rank = rank
                best_row = row
                best_score = score
                best_debit_sub_matches = debit_sub_matches
                best_credit_sub_matches = credit_sub_matches
                best_text_matches = text_matches

    if best_row is None:
        if diagnostic["科目一致"] == 0:
            reason = "同じ借方科目・貸方科目の過去仕訳がありません"
        else:
            reason = "科目一致したが摘要/補助に取引先名がなく不採用"

        diagnostic["理由"] = reason
        return None, diagnostic

    diagnostic["DB雛形"] = "あり"
    diagnostic["理由"] = "生成仕訳に近い過去仕訳をDB雛形として参照します"
    diagnostic["摘要一致有無"] = "あり" if best_text_matches else "なし"
    diagnostic["補助一致"] = (
        "借方・貸方"
        if best_debit_sub_matches and best_credit_sub_matches
        else "借方"
        if best_debit_sub_matches
        else "貸方"
        if best_credit_sub_matches
        else "なし"
    )
    diagnostic["採用スコア"] = best_score
    diagnostic["雛形日付"] = best_row.get(COL_DATE, "")
    diagnostic["雛形摘要"] = best_row.get(COL_SUMMARY, "")
    diagnostic["雛形伝票摘要"] = best_row.get("伝票摘要", "")
    diagnostic["雛形形式"] = best_row.get("形式", "")
    diagnostic["雛形借方部門"] = best_row.get("借方部門名", "")
    diagnostic["雛形貸方部門"] = best_row.get("貸方部門名", "")
    diagnostic["雛形借方税コード"] = best_row.get("借方消費税コード", "")
    diagnostic["雛形貸方税コード"] = best_row.get("貸方消費税コード", "")
    diagnostic["雛形品質"] = get_receivable_template_quality(best_row)
    if diagnostic["補助一致"] == "なし":
        diagnostic["採用理由"] = "補助不一致だが摘要一致で雛形採用"
    else:
        diagnostic["採用理由"] = "科目一致・摘要一致で雛形採用"

    if diagnostic["雛形品質"] == 0:
        diagnostic["採用理由"] += "（候補はあったが雛形品質が低い）"

    return best_row, diagnostic


def find_receivable_template_row(journal, source_candidates, customer_name, records=()):

    best_row, _ = find_receivable_template_match(
        journal,
        customer_name,
        source_candidates, records
    )

    if best_row is None:
        return build_empty_epson_template_row()

    return {
        column: best_row.get(column, "")
        for column in EPSON_COLUMNS
    }
