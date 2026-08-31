import copy
import csv
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from pandas.testing import assert_frame_equal


SRC_DIR = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

import receivable_options_service as service  # noqa: E402
from receivable_options_service import (  # noqa: E402
    build_receipt_account_options,
    build_receivable_difference_account_options,
    build_safe_receivable_difference_options,
    is_receivable_difference_recommend_excluded,
    is_receivable_difference_recommendable,
    load_payment_accounts,
    resolve_unique_account_options,
)


def master_row(code, name, category=""):
    return {"code": str(code), "name": name, "category": category}


def candidate(**overrides):
    result = {
        "未収科目": "未収運賃",
        "未収補助": "A商事",
        "部門": "本社",
        "摘要": "alpha beta",
    }
    result.update(overrides)
    return result


def history_row(account, *, side="debit", summary="alpha", **overrides):
    result = {
        service.COL_DEBIT: account if side == "debit" else "普通預金",
        service.COL_CREDIT: account if side == "credit" else "未収運賃",
        service.COL_DEBIT_SUB: "",
        service.COL_CREDIT_SUB: "",
        service.COL_SUMMARY: summary,
        "伝票摘要": "",
    }
    result.update(overrides)
    return result


def records(*rows):
    return [{"rows": list(rows)}]


def recommend(
    master,
    transaction_records=(),
    *,
    side="debit",
    default="その他",
    candidates=None,
    customer="A商事",
    top_n=5,
):
    return build_receivable_difference_account_options(
        master,
        list(transaction_records),
        customer,
        [candidate()] if candidates is None else candidates,
        side,
        default,
        top_n,
    )


def legacy_recommendable(account, side, categories, codes):
    excluded = {
        "資金複合", "諸口", "普通預金", "当座預金", "現金",
        "未収運賃", "未収金", "売掛金",
    }
    if account in excluded or "未収" in account or "売掛" in account:
        return False
    if side == "credit" and account in {"未払金", "買掛金", "未払費用", "預り金"}:
        return False
    category = categories.get(account, "")
    if category:
        return category == "費用" if side == "debit" else category in {"負債", "収益"}
    try:
        code_number = int(str(codes.get(account, "")).strip())
    except Exception:
        code_number = 0
    if side == "debit":
        return 400 <= code_number < 600 or account in {"支払手数料", "雑費"}
    return (
        200 <= code_number < 300
        or account in {"仮受金", "雑収入"}
        or account.endswith("収入")
    )


def legacy_difference_options(
    master, transaction_records, customer, candidates_value, side, default, top_n=5
):
    codes = {row["name"]: row["code"] for row in master}
    categories = {row["name"]: row.get("category", "") for row in master}
    allowed = list(dict.fromkeys(
        row["name"] for row in master if row["name"] not in {"資金複合", "諸口"}
    ))
    if not allowed:
        return [], set(), ""
    if default not in allowed:
        default = allowed[0]
    context = " ".join(
        [str(customer or "")]
        + [
            str(item.get(column, "") or "")
            for item in candidates_value
            for column in ("未収科目", "未収補助", "部門", "摘要")
        ]
    )
    context_tokens = set(service.tokenize(context))
    target = service.COL_DEBIT if side == "debit" else service.COL_CREDIT
    scores = {}
    counts = {}
    first_seen = {}
    for record in transaction_records:
        for row in record.get("rows", []):
            account = str(row.get(target, "") or "").strip()
            if (
                not account
                or account not in allowed
                or not legacy_recommendable(account, side, categories, codes)
            ):
                continue
            row_text = " ".join(str(row.get(column, "") or "") for column in (
                service.COL_SUMMARY,
                "伝票摘要",
                service.COL_DEBIT_SUB,
                service.COL_CREDIT_SUB,
                service.COL_DEBIT,
                service.COL_CREDIT,
            ))
            score = len(context_tokens & set(service.tokenize(row_text)))
            if score <= 0:
                continue
            if account not in first_seen:
                first_seen[account] = len(first_seen)
            scores[account] = scores.get(account, 0) + score
            counts[account] = counts.get(account, 0) + 1
    recommended = sorted(
        scores,
        key=lambda account: (-scores[account], -counts[account], first_seen[account]),
    )[:top_n]
    if legacy_recommendable(default, side, categories, codes) and default not in recommended:
        recommended.insert(0, default)
        recommended = recommended[:top_n]
    options = recommended + ([default] if default not in recommended else []) + [
        account for account in allowed if account not in recommended and account != default
    ]
    return options, set(recommended), default


class PaymentAccountLoaderTests(unittest.TestCase):
    def write_csv(self, rows, *, header="科目", encoding="utf-8"):
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        path = Path(temporary_directory.name) / "payment_accounts.csv"
        with path.open("w", encoding=encoding, newline="") as file:
            writer = csv.writer(file)
            writer.writerow([header])
            writer.writerows([[row] for row in rows])
        return path

    def test_loader_preserves_set_and_sorted_compatibility(self):
        path = self.write_csv(["普通預金", "当座預金", "現金", "電子記録債権"])
        self.assertEqual(
            load_payment_accounts(path),
            ["当座預金", "普通預金", "現金", "電子記録債権"],
        )

    def test_loader_removes_duplicates(self):
        path = self.write_csv(["普通預金", "普通預金", " 当座預金 "])
        self.assertEqual(load_payment_accounts(path), ["当座預金", "普通預金"])

    def test_loader_skips_empty_and_nan_values(self):
        path = self.write_csv(["", "   ", "NaN", "ｎａｎ", "現金"])
        self.assertEqual(load_payment_accounts(path), ["現金", "ｎａｎ"])

    def test_loader_accepts_utf8_bom(self):
        path = self.write_csv(["普通預金"], encoding="utf-8-sig")
        self.assertEqual(load_payment_accounts(path), ["普通預金"])

    def test_loader_with_missing_column_matches_legacy_empty_result(self):
        path = self.write_csv(["普通預金"], header="name")
        self.assertEqual(load_payment_accounts(path), [])


class ReceiptAccountOptionTests(unittest.TestCase):
    def setUp(self):
        self.master = [
            master_row(110, "当座預金"),
            master_row(114, "普通預金"),
            master_row(100, "現金"),
        ]

    def test_unique_name_resolves_to_code_name_dto(self):
        options, invalid = resolve_unique_account_options(
            ["普通預金"], self.master
        )
        self.assertEqual(options, [{"code": "114", "name": "普通預金"}])
        self.assertEqual(invalid, [])

    def test_missing_name_is_excluded(self):
        options, invalid = resolve_unique_account_options(
            ["電子記録債権"], self.master
        )
        self.assertEqual(options, [])
        self.assertEqual(invalid, ["電子記録債権"])

    def test_ambiguous_name_with_distinct_codes_is_excluded(self):
        master = self.master + [master_row(115, "普通預金")]
        options, invalid = resolve_unique_account_options(["普通預金"], master)
        self.assertEqual(options, [])
        self.assertEqual(invalid, ["普通預金"])

    def test_duplicate_name_with_same_code_is_unique_resolution(self):
        master = self.master + [master_row(114, "普通預金")]
        options, invalid = resolve_unique_account_options(["普通預金"], master)
        self.assertEqual(options, [{"code": "114", "name": "普通預金"}])
        self.assertEqual(invalid, [])

    def test_receipt_options_are_sorted_and_deduplicated(self):
        result = build_receipt_account_options(
            ["現金", "普通預金", "当座預金", "現金"], self.master
        )
        self.assertEqual(
            [item["name"] for item in result["receipt_accounts"]],
            ["当座預金", "普通預金", "現金"],
        )

    def test_default_is_ordinary_deposit_when_available(self):
        result = build_receipt_account_options(
            ["現金", "普通預金", "当座預金"], self.master
        )
        self.assertEqual(result["default_receipt_account"], "普通預金")

    def test_default_is_first_valid_option_without_ordinary_deposit(self):
        result = build_receipt_account_options(
            ["現金", "当座預金"], self.master
        )
        self.assertEqual(result["default_receipt_account"], "当座預金")

    def test_default_is_none_when_no_valid_options_exist(self):
        result = build_receipt_account_options(["電子記録債権"], self.master)
        self.assertIsNone(result["default_receipt_account"])
        self.assertEqual(result["receipt_accounts"], [])

    def test_invalid_names_are_retained_as_internal_diagnostics(self):
        result = build_receipt_account_options(
            ["普通預金", "電子記録債権"], self.master
        )
        self.assertEqual(
            result["invalid_receipt_account_names"], ["電子記録債権"]
        )

    def test_malformed_master_snapshot_yields_no_selectable_options(self):
        result = build_receipt_account_options(["普通預金"], {"accounts": "bad"})
        self.assertEqual(result["receipt_accounts"], [])
        self.assertEqual(result["invalid_receipt_account_names"], ["普通預金"])

    def test_dataframe_master_input_is_not_mutated(self):
        dataframe = pd.DataFrame(self.master)
        before = dataframe.copy(deep=True)
        build_receipt_account_options(["普通預金"], dataframe)
        assert_frame_equal(dataframe, before)

    def test_mapping_master_snapshot_is_supported(self):
        result = build_receipt_account_options(
            ["普通預金"], {"普通預金": "114"}
        )
        self.assertEqual(
            result["receipt_accounts"],
            [{"code": "114", "name": "普通預金"}],
        )


class DifferenceEligibilityTests(unittest.TestCase):
    def test_debit_category_requires_expense(self):
        codes = {"候補": "100"}
        self.assertTrue(
            is_receivable_difference_recommendable(
                "候補", "debit", {"候補": "費用"}, codes
            )
        )
        self.assertFalse(
            is_receivable_difference_recommendable(
                "候補", "debit", {"候補": "資産"}, codes
            )
        )

    def test_credit_category_accepts_liability_and_revenue(self):
        codes = {"候補": "500"}
        for category in ("負債", "収益"):
            with self.subTest(category=category):
                self.assertTrue(
                    is_receivable_difference_recommendable(
                        "候補", "credit", {"候補": category}, codes
                    )
                )

    def test_nonempty_category_takes_priority_over_code_band(self):
        self.assertFalse(
            is_receivable_difference_recommendable(
                "候補", "debit", {"候補": "資産"}, {"候補": "532"}
            )
        )
        self.assertFalse(
            is_receivable_difference_recommendable(
                "候補", "credit", {"候補": "費用"}, {"候補": "207"}
            )
        )

    def test_empty_category_debit_uses_code_band_and_fixed_names(self):
        categories = {}
        self.assertTrue(
            is_receivable_difference_recommendable(
                "候補", "debit", categories, {"候補": "400"}
            )
        )
        self.assertTrue(
            is_receivable_difference_recommendable(
                "支払手数料", "debit", categories, {"支払手数料": "0"}
            )
        )

    def test_empty_category_credit_uses_code_band_and_name_conditions(self):
        for name, code in (("候補", "299"), ("雑収入", "0"), ("受取収入", "0")):
            with self.subTest(name=name):
                self.assertTrue(
                    is_receivable_difference_recommendable(
                        name, "credit", {}, {name: code}
                    )
                )

    def test_common_excluded_accounts_and_name_fragments_are_rejected(self):
        for name in ("資金複合", "諸口", "普通預金", "未収保険料", "売掛運賃"):
            with self.subTest(name=name):
                self.assertTrue(is_receivable_difference_recommend_excluded(name))

    def test_overpayment_specific_accounts_are_rejected(self):
        for name in ("未払金", "買掛金", "未払費用", "預り金"):
            with self.subTest(name=name):
                self.assertFalse(
                    is_receivable_difference_recommendable(
                        name, "credit", {name: "負債"}, {name: "204"}
                    )
                )


class DifferenceRecommendationTests(unittest.TestCase):
    def test_shortage_inserts_payment_fee_default(self):
        master = [master_row(532, "支払手数料"), master_row(100, "その他")]
        options, recommended, selected = recommend(
            master, default="支払手数料"
        )
        self.assertEqual(options[0], "支払手数料")
        self.assertEqual(recommended, {"支払手数料"})
        self.assertEqual(selected, "支払手数料")

    def test_overpayment_inserts_suspense_receipt_default(self):
        master = [master_row(207, "仮受金"), master_row(500, "その他")]
        options, recommended, selected = recommend(
            master, side="credit", default="仮受金"
        )
        self.assertEqual(options[0], "仮受金")
        self.assertEqual(recommended, {"仮受金"})
        self.assertEqual(selected, "仮受金")

    def test_history_total_score_is_primary_order(self):
        master = [
            master_row(500, "高得点", "費用"),
            master_row(501, "低得点", "費用"),
            master_row(100, "その他", "資産"),
        ]
        transaction_records = records(
            history_row("低得点", summary="alpha"),
            history_row("高得点", summary="alpha beta"),
        )
        options, recommended, _ = recommend(master, transaction_records)
        self.assertEqual(options[:2], ["高得点", "低得点"])
        self.assertEqual(recommended, {"高得点", "低得点"})

    def test_equal_score_uses_matching_row_count(self):
        master = [
            master_row(500, "一行", "費用"),
            master_row(501, "二行", "費用"),
            master_row(100, "その他", "資産"),
        ]
        transaction_records = records(
            history_row("一行", summary="alpha beta"),
            history_row("二行", summary="alpha"),
            history_row("二行", summary="beta"),
        )
        options, _, _ = recommend(master, transaction_records)
        self.assertEqual(options[:2], ["二行", "一行"])

    def test_equal_score_and_count_use_first_seen(self):
        master = [
            master_row(500, "後master", "費用"),
            master_row(501, "先history", "費用"),
            master_row(100, "その他", "資産"),
        ]
        transaction_records = records(
            history_row("先history", summary="alpha"),
            history_row("後master", summary="alpha"),
        )
        options, _, _ = recommend(master, transaction_records)
        self.assertEqual(options[:2], ["先history", "後master"])

    def test_history_recommendations_are_limited_to_top_five(self):
        master = [master_row(500 + index, f"科目{index}", "費用") for index in range(6)]
        transaction_records = records(*[
            history_row(f"科目{index}", summary="alpha") for index in range(6)
        ])
        _, recommended, _ = recommend(
            master, transaction_records, default="科目0"
        )
        self.assertEqual(len(recommended), 5)
        self.assertNotIn("科目5", recommended)

    def test_recommendable_fixed_default_is_inserted_at_front(self):
        master = [
            master_row(500, "履歴", "費用"),
            master_row(532, "支払手数料"),
        ]
        options, recommended, _ = recommend(
            master,
            records(history_row("履歴", summary="alpha")),
            default="支払手数料",
        )
        self.assertEqual(options[:2], ["支払手数料", "履歴"])
        self.assertEqual(recommended, {"支払手数料", "履歴"})

    def test_default_insertion_retruncates_to_top_five(self):
        master = [master_row(500 + index, f"履歴{index}", "費用") for index in range(5)]
        master.append(master_row(532, "支払手数料"))
        transaction_records = records(*[
            history_row(f"履歴{index}", summary="alpha") for index in range(5)
        ])
        options, recommended, _ = recommend(
            master, transaction_records, default="支払手数料"
        )
        self.assertEqual(options[0], "支払手数料")
        self.assertEqual(len(recommended), 5)
        self.assertNotIn("履歴4", recommended)

    def test_remaining_options_keep_supplied_master_order(self):
        master = [
            master_row(100, "Z科目", "資産"),
            master_row(101, "A科目", "資産"),
            master_row(102, "M科目", "資産"),
        ]
        options, recommended, selected = recommend(master, default="A科目")
        self.assertEqual(options, ["A科目", "Z科目", "M科目"])
        self.assertEqual(recommended, set())
        self.assertEqual(selected, "A科目")

    def test_account_names_are_deduplicated_by_membership(self):
        master = [
            master_row(500, "重複", "費用"),
            master_row(500, "重複", "費用"),
            master_row(100, "その他", "資産"),
        ]
        options, _, _ = recommend(
            master,
            records(
                history_row("重複", summary="alpha"),
                history_row("重複", summary="beta"),
            ),
        )
        self.assertEqual(options.count("重複"), 1)

    def test_shortage_reads_debit_account_only(self):
        master = [master_row(500, "借方候補", "費用"), master_row(501, "貸方候補", "費用")]
        transaction_records = records({
            **history_row("借方候補", summary="alpha"),
            service.COL_CREDIT: "貸方候補",
        })
        _, recommended, _ = recommend(master, transaction_records)
        self.assertIn("借方候補", recommended)
        self.assertNotIn("貸方候補", recommended)

    def test_overpayment_reads_credit_account_only(self):
        master = [
            master_row(200, "借方候補", "負債"),
            master_row(201, "貸方候補", "負債"),
            master_row(100, "その他", "資産"),
        ]
        transaction_records = records({
            **history_row("貸方候補", side="credit", summary="alpha"),
            service.COL_DEBIT: "借方候補",
        })
        _, recommended, _ = recommend(
            master, transaction_records, side="credit"
        )
        self.assertIn("貸方候補", recommended)
        self.assertNotIn("借方候補", recommended)

    def test_customer_and_all_candidate_context_fields_contribute_tokens(self):
        master = [master_row(500, "候補", "費用")]
        contexts = [
            ("customer-token", [candidate(摘要="")]),
            ("account-token", [candidate(未収科目="account-token", 摘要="")]),
            ("sub-token", [candidate(未収補助="sub-token", 摘要="")]),
            ("department-token", [candidate(部門="department-token", 摘要="")]),
            ("summary-token", [candidate(摘要="summary-token")]),
        ]
        for token, candidates_value in contexts:
            with self.subTest(token=token):
                customer = token if token == "customer-token" else ""
                transaction_records = records(history_row("候補", summary=token))
                _, recommended, _ = recommend(
                    master,
                    transaction_records,
                    candidates=candidates_value,
                    customer=customer,
                )
                self.assertIn("候補", recommended)

    def test_candidates_and_transactions_are_not_mutated(self):
        master = [master_row(500, "候補", "費用")]
        candidates_value = [candidate()]
        transaction_records = records(history_row("候補", summary="alpha"))
        candidates_before = copy.deepcopy(candidates_value)
        records_before = copy.deepcopy(transaction_records)
        recommend(master, transaction_records, candidates=candidates_value)
        self.assertEqual(candidates_value, candidates_before)
        self.assertEqual(transaction_records, records_before)

    def test_empty_master_has_no_options_or_default(self):
        self.assertEqual(recommend([], default="支払手数料"), ([], set(), ""))


class SafeRecommendationTests(unittest.TestCase):
    def test_safe_recommendations_exclude_ambiguous_master_name(self):
        master = [
            master_row(500, "候補", "費用"),
            master_row(501, "候補", "費用"),
            master_row(100, "その他", "資産"),
        ]
        result = build_safe_receivable_difference_options(
            master,
            records(history_row("候補", summary="alpha")),
            "A商事",
            [candidate()],
            "debit",
            "その他",
        )
        self.assertNotIn(
            "候補", [item["name"] for item in result["difference_account_options"]]
        )
        self.assertEqual(result["invalid_difference_account_names"], ["候補"])

    def test_safe_resolver_excludes_orphan_name(self):
        options, invalid = resolve_unique_account_options(
            ["候補", "孤立"], [master_row(500, "候補", "費用")]
        )
        self.assertEqual(options, [{"code": "500", "name": "候補"}])
        self.assertEqual(invalid, ["孤立"])

    def test_safe_recommendation_dto_contains_only_code_and_name(self):
        master = [master_row(500, "候補", "費用")]
        result = build_safe_receivable_difference_options(
            master,
            records(history_row("候補", summary="alpha")),
            "A商事",
            [candidate()],
            "debit",
            "候補",
        )
        self.assertEqual(
            result["recommended_difference_accounts"],
            [{"code": "500", "name": "候補"}],
        )

    def test_production_wrapper_injects_engine_records_into_pure_core(self):
        master = [master_row(500, "候補", "費用")]
        loaded = records(history_row("候補", summary="alpha"))
        with patch.object(service, "load_data", return_value=(loaded, {}, {})):
            options, recommended, _ = (
                service.load_receivable_difference_account_options(
                    master,
                    "A商事",
                    [candidate()],
                    "debit",
                    "候補",
                )
            )
        self.assertEqual(options[0], "候補")
        self.assertEqual(recommended, {"候補"})


class StreamlitParityTests(unittest.TestCase):
    def test_shared_core_matches_pre_extraction_characterization(self):
        cases = [
            {
                "name": "shortage_history_category",
                "master": [master_row(500, "履歴", "費用"), master_row(532, "支払手数料")],
                "records": records(history_row("履歴", summary="alpha beta")),
                "side": "debit",
                "default": "支払手数料",
            },
            {
                "name": "shortage_no_history_empty_category",
                "master": [master_row(532, "支払手数料"), master_row(100, "その他")],
                "records": [],
                "side": "debit",
                "default": "支払手数料",
            },
            {
                "name": "overpayment_history_category",
                "master": [master_row(200, "履歴", "収益"), master_row(207, "仮受金")],
                "records": records(history_row("履歴", side="credit", summary="alpha")),
                "side": "credit",
                "default": "仮受金",
            },
            {
                "name": "overpayment_no_history_empty_category",
                "master": [master_row(207, "仮受金"), master_row(100, "その他")],
                "records": [],
                "side": "credit",
                "default": "仮受金",
            },
            {
                "name": "history_tie",
                "master": [master_row(500, "先", "費用"), master_row(501, "後", "費用")],
                "records": records(
                    history_row("後", summary="alpha"),
                    history_row("先", summary="alpha"),
                ),
                "side": "debit",
                "default": "先",
            },
            {
                "name": "fallback_default",
                "master": [master_row(100, "先頭", "資産"), master_row(500, "次", "費用")],
                "records": [],
                "side": "debit",
                "default": "存在しない",
            },
        ]
        for case in cases:
            with self.subTest(case=case["name"]):
                expected = legacy_difference_options(
                    case["master"],
                    case["records"],
                    "A商事",
                    [candidate()],
                    case["side"],
                    case["default"],
                )
                actual = build_receivable_difference_account_options(
                    case["master"],
                    case["records"],
                    "A商事",
                    [candidate()],
                    case["side"],
                    case["default"],
                )
                self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
