"""未収消込のFIFO候補と抽象仕訳Previewを副作用なしで生成する。"""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from typing import Any

import pandas as pd

from receivable_engine import build_receivable_journal_rows


PARTIAL_SETTLEMENT_MODE = "部分消込（残額を未収に残す）"
DIFFERENCE_ACCOUNT_MODE = "差額を科目で処理する"

EXACT_MATCH_PATTERN = "完全一致"
PARTIAL_SETTLEMENT_PATTERN = "部分消込"
SHORTAGE_DIFFERENCE_PATTERN = "不足差額処理"
OVERPAYMENT_PATTERN = "過入金"


def parse_receivable_payment_amount(value: Any) -> int | None:
    """現行Streamlitと同じ規則で入金額を正の整数へ変換する。"""

    if value is None:
        return None

    normalized = str(value)
    for old, new in [
        (",", ""),
        ("，", ""),
        ("円", ""),
        ("￥", ""),
        ("¥", ""),
    ]:
        normalized = normalized.replace(old, new)

    normalized = "".join(normalized.split())

    if not normalized or not normalized.isdigit():
        return None

    amount = int(normalized)
    if amount <= 0:
        return None

    return amount


def _snapshot_dataframe(
    receivable_snapshot: pd.DataFrame | Sequence[Mapping[str, Any]],
) -> pd.DataFrame:
    if isinstance(receivable_snapshot, pd.DataFrame):
        return receivable_snapshot.copy(deep=True)

    return pd.DataFrame(copy.deepcopy(list(receivable_snapshot)))


def prepare_customer_receivables(
    receivable_snapshot: pd.DataFrame | Sequence[Mapping[str, Any]],
    customer_name: str,
) -> pd.DataFrame:
    """currentのスナップショットから現行UIと同じ対象明細を作る。"""

    receivables_df = _snapshot_dataframe(receivable_snapshot)
    if receivables_df.empty:
        return receivables_df.copy()

    if "得意先名" in receivables_df.columns:
        customer_df = receivables_df[
            receivables_df["得意先名"] == customer_name
        ].copy()
    else:
        # 現行app.pyは得意先で絞った後、候補用DataFrameから列を外す。
        customer_df = receivables_df.copy()

    if customer_df.empty:
        return customer_df

    if "請求日" not in customer_df.columns:
        customer_df["請求日"] = ""

    customer_df["残高"] = pd.to_numeric(
        customer_df["残高"].astype(str).str.replace(",", "", regex=False),
        errors="coerce",
    ).fillna(0).astype(int)

    customer_df = customer_df[
        (customer_df["残高"] > 0)
        & (customer_df["ステータス"] != "完了")
    ].copy()

    if customer_df.empty:
        return customer_df

    customer_df["請求金額"] = pd.to_numeric(
        customer_df["請求金額"].astype(str).str.replace(",", "", regex=False),
        errors="coerce",
    ).fillna(0).astype(int)

    return customer_df


def build_receivable_fifo_candidates(
    receivable_snapshot: pd.DataFrame | Sequence[Mapping[str, Any]],
    customer_name: str,
    payment_amount: Any,
) -> dict[str, Any]:
    """現行app.pyのstable FIFO候補を入力非破壊で生成する。"""

    parsed_payment_amount = parse_receivable_payment_amount(payment_amount)
    if parsed_payment_amount is None:
        raise ValueError("入金額を入力してください")

    fifo_df = prepare_customer_receivables(
        receivable_snapshot,
        customer_name,
    )

    if fifo_df.empty:
        return {
            "target_candidates": [],
            "partial_candidates": [],
            "total_receivable_balance": 0,
            "partial_total": 0,
            "remaining_payment": parsed_payment_amount,
            "difference": parsed_payment_amount,
        }

    fifo_df["_請求日"] = pd.to_datetime(
        fifo_df["請求日"],
        errors="coerce",
    )
    fifo_df["_表示順"] = range(len(fifo_df))
    fifo_df = fifo_df.sort_values(
        ["_請求日", "_表示順"],
        na_position="last",
        kind="stable",
    )

    target_candidates: list[dict[str, Any]] = []
    partial_candidates: list[dict[str, Any]] = []
    remaining = int(parsed_payment_amount)

    for _, detail in fifo_df.iterrows():
        target_amount = int(detail["残高"])
        if target_amount <= 0:
            continue

        target_candidate = {
            "コード": detail["コード"],
            "未収ID": detail["未収ID"],
            "請求日": detail["請求日"],
            "請求額": detail["請求金額"],
            "残高": detail["残高"],
            "消込予定": target_amount,
            "未収科目": detail["未収科目"],
            "未収補助": detail["未収補助"],
            "部門": detail["部門"],
            "取引先": customer_name,
            "摘要": detail.get("摘要", ""),
        }
        target_candidates.append(target_candidate)

        if remaining <= 0:
            continue

        scheduled_amount = min(target_amount, remaining)
        if scheduled_amount <= 0:
            continue

        partial_candidate = target_candidate.copy()
        partial_candidate["消込予定"] = scheduled_amount
        partial_candidates.append(partial_candidate)
        remaining -= scheduled_amount

    total_receivable_balance = sum(
        item["消込予定"] for item in target_candidates
    )
    partial_total = sum(
        item["消込予定"] for item in partial_candidates
    )

    return {
        "target_candidates": target_candidates,
        "partial_candidates": partial_candidates,
        "total_receivable_balance": total_receivable_balance,
        "partial_total": partial_total,
        "remaining_payment": remaining,
        "difference": parsed_payment_amount - total_receivable_balance,
    }


def build_receivable_preview_from_fifo(
    fifo_result: Mapping[str, Any],
    customer_name: str,
    payment_amount: Any,
    settlement_date: Any,
    receipt_account: str,
    mode: str | None = None,
    difference_account: str | None = None,
    difference_summary: str | None = None,
) -> dict[str, Any]:
    """FIFO結果へ現行UIのmodeを適用し、抽象仕訳Previewを作る。"""

    parsed_payment_amount = parse_receivable_payment_amount(payment_amount)
    if parsed_payment_amount is None:
        raise ValueError("入金額を入力してください")

    target_candidates = copy.deepcopy(
        list(fifo_result.get("target_candidates", []))
    )
    partial_candidates = copy.deepcopy(
        list(fifo_result.get("partial_candidates", []))
    )
    total_receivable_balance = sum(
        int(item["消込予定"]) for item in target_candidates
    )
    original_difference = (
        parsed_payment_amount - total_receivable_balance
    )

    difference_side = None
    effective_mode = mode

    if original_difference == 0:
        pattern = EXACT_MATCH_PATTERN
        effective_mode = None
        source_candidates = target_candidates
    elif original_difference < 0:
        if mode in (None, PARTIAL_SETTLEMENT_MODE):
            pattern = PARTIAL_SETTLEMENT_PATTERN
            effective_mode = PARTIAL_SETTLEMENT_MODE
            source_candidates = partial_candidates
        elif mode == DIFFERENCE_ACCOUNT_MODE:
            pattern = SHORTAGE_DIFFERENCE_PATTERN
            source_candidates = target_candidates
            difference_side = "debit"
        else:
            raise ValueError("処理方法が不正です")
    else:
        if mode not in (None, DIFFERENCE_ACCOUNT_MODE):
            raise ValueError("処理方法が不正です")
        pattern = OVERPAYMENT_PATTERN
        effective_mode = DIFFERENCE_ACCOUNT_MODE
        source_candidates = target_candidates
        difference_side = "credit"

    target_total = sum(
        int(item["消込予定"]) for item in source_candidates
    )
    difference = parsed_payment_amount - target_total
    rows = build_receivable_journal_rows(
        source_candidates,
        parsed_payment_amount,
        receipt_account,
        customer_name,
        difference_account,
        difference_side,
        difference_summary,
    )

    return {
        "customer_name": customer_name,
        "settlement_date": settlement_date,
        "payment_amount": parsed_payment_amount,
        "receipt_account": receipt_account,
        "target_total": target_total,
        "difference": difference,
        "mode": effective_mode,
        "pattern": pattern,
        "source_candidates": copy.deepcopy(source_candidates),
        "rows": copy.deepcopy(rows),
        "target_candidates": target_candidates,
        "partial_candidates": partial_candidates,
        "total_receivable_balance": total_receivable_balance,
        "partial_total": sum(
            int(item["消込予定"]) for item in partial_candidates
        ),
        "remaining_payment": int(
            fifo_result.get("remaining_payment", 0)
        ),
    }


def build_receivable_preview(
    receivable_snapshot: pd.DataFrame | Sequence[Mapping[str, Any]],
    customer_name: str,
    payment_amount: Any,
    settlement_date: Any,
    receipt_account: str,
    mode: str | None = None,
    difference_account: str | None = None,
    difference_summary: str | None = None,
) -> dict[str, Any]:
    """未収スナップショットから副作用なしでPreview DTOを生成する。"""

    fifo_result = build_receivable_fifo_candidates(
        receivable_snapshot,
        customer_name,
        payment_amount,
    )
    return build_receivable_preview_from_fifo(
        fifo_result,
        customer_name,
        payment_amount,
        settlement_date,
        receipt_account,
        mode,
        difference_account,
        difference_summary,
    )
