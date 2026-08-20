"""会計年度と検索DB保持境界を計算する副作用のない関数群。"""

from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime
from typing import Any


JOURNAL_DATE_FORMATS = ("%Y%m%d", "%Y/%m/%d", "%Y-%m-%d")
KEEP_PAST_FISCAL_YEARS = 3


class InvalidJournalDateError(ValueError):
    """伝票日付が会計年度判定に使えない場合のデータ保護エラー。"""


def validate_fiscal_year_start_month(start_month: int) -> int:
    """会計年度開始月が1～12であることを保証する。"""

    if isinstance(start_month, bool) or not isinstance(start_month, int):
        raise ValueError("会計年度開始月は1～12の整数で指定してください")
    if not 1 <= start_month <= 12:
        raise ValueError("会計年度開始月は1～12で指定してください")
    return start_month


def parse_journal_date(value: Any) -> date | None:
    """既存仕訳で使われる日付形式を解析し、不正値は補正せずNoneにする。"""

    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    text = str(value or "").strip()
    for date_format in JOURNAL_DATE_FORMATS:
        try:
            return datetime.strptime(text, date_format).date()
        except ValueError:
            continue
    return None


def require_journal_date(value: Any) -> date:
    """伝票日付を解析し、不正値なら更新停止用の例外を送出する。"""

    journal_date = parse_journal_date(value)
    if journal_date is None:
        raise InvalidJournalDateError(
            f"会計年度を判定できない伝票日付があります: {value!r}"
        )
    return journal_date


def get_fiscal_year(value: date | datetime, start_month: int) -> int:
    """日付が属する会計年度を開始年で返す。"""

    start_month = validate_fiscal_year_start_month(start_month)
    target_date = value.date() if isinstance(value, datetime) else value
    if not isinstance(target_date, date):
        raise TypeError("会計年度の計算にはdateまたはdatetimeが必要です")
    return target_date.year if target_date.month >= start_month else target_date.year - 1


def get_current_fiscal_year(today: date | datetime, start_month: int) -> int:
    """指定日現在の会計年度を返す。"""

    return get_fiscal_year(today, start_month)


def get_fiscal_year_period(fiscal_year: int, start_month: int) -> tuple[date, date]:
    """会計年度の開始日と終了日を返す。"""

    start_month = validate_fiscal_year_start_month(start_month)
    start_date = date(fiscal_year, start_month, 1)
    end_month = 12 if start_month == 1 else start_month - 1
    end_year = fiscal_year if start_month == 1 else fiscal_year + 1
    end_date = date(end_year, end_month, monthrange(end_year, end_month)[1])
    return start_date, end_date


def get_retention_start_date(
    today: date | datetime,
    start_month: int,
    keep_past_years: int = KEEP_PAST_FISCAL_YEARS,
) -> date:
    """現在年度と過去N会計年度を保持する場合の開始日を返す。"""

    if isinstance(keep_past_years, bool) or not isinstance(keep_past_years, int):
        raise ValueError("過去保持年度数は0以上の整数で指定してください")
    if keep_past_years < 0:
        raise ValueError("過去保持年度数は0以上で指定してください")

    current_fiscal_year = get_current_fiscal_year(today, start_month)
    retention_fiscal_year = current_fiscal_year - keep_past_years
    return date(retention_fiscal_year, start_month, 1)


def build_fiscal_year_info(
    today: date | datetime,
    start_month: int,
    keep_past_years: int = KEEP_PAST_FISCAL_YEARS,
) -> dict[str, int | str]:
    """API表示に必要な会計年度情報をまとめて返す。"""

    start_month = validate_fiscal_year_start_month(start_month)
    current_fiscal_year = get_current_fiscal_year(today, start_month)
    fiscal_start, fiscal_end = get_fiscal_year_period(
        current_fiscal_year,
        start_month,
    )
    retention_start = get_retention_start_date(
        today,
        start_month,
        keep_past_years,
    )
    return {
        "fiscal_year_start_month": start_month,
        "fiscal_year_end_month": fiscal_end.month,
        "current_fiscal_year": current_fiscal_year,
        "current_fiscal_year_start": fiscal_start.isoformat(),
        "current_fiscal_year_end": fiscal_end.isoformat(),
        "retention_start_date": retention_start.isoformat(),
        "keep_past_fiscal_years": keep_past_years,
    }
