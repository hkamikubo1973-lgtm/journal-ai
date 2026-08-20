import csv
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch


SRC_DIR = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

from fiscal_year import (  # noqa: E402
    build_fiscal_year_info,
    get_fiscal_year,
    get_retention_start_date,
    InvalidJournalDateError,
    parse_journal_date,
    validate_fiscal_year_start_month,
)
import engine  # noqa: E402
from columns import EPSON_COLUMNS  # noqa: E402


class FiscalYearTest(unittest.TestCase):
    def test_february_boundaries(self):
        self.assertEqual(get_fiscal_year(date(2026, 1, 31), 2), 2025)
        self.assertEqual(get_fiscal_year(date(2026, 2, 1), 2), 2026)
        self.assertEqual(
            get_retention_start_date(date(2026, 8, 21), 2),
            date(2023, 2, 1),
        )
        info = build_fiscal_year_info(date(2026, 8, 21), 2)
        self.assertEqual(info["current_fiscal_year_start"], "2026-02-01")
        self.assertEqual(info["current_fiscal_year_end"], "2027-01-31")

    def test_requested_retention_boundaries(self):
        rows = [
            {"伝票日付": value, "借方金額": "1", "貸方金額": "1"}
            for value in (
                "20230131",
                "20230201",
                "20240131",
                "20240201",
                "20260131",
                "20260201",
            )
        ]
        kept = engine.keep_recent_years(
            rows,
            today=date(2026, 8, 21),
            start_month=2,
        )
        self.assertEqual(
            [row["伝票日付"] for row in kept],
            ["20230201", "20240131", "20240201", "20260131", "20260201"],
        )
        self.assertIs(kept[0], rows[1])

    def test_start_month_variants(self):
        self.assertEqual(get_fiscal_year(date(2026, 1, 1), 1), 2026)
        self.assertEqual(get_fiscal_year(date(2026, 3, 31), 4), 2025)
        self.assertEqual(get_fiscal_year(date(2026, 4, 1), 4), 2026)
        self.assertEqual(get_fiscal_year(date(2026, 11, 30), 12), 2025)
        self.assertEqual(get_fiscal_year(date(2026, 12, 1), 12), 2026)

    def test_next_fiscal_year_transition(self):
        before = build_fiscal_year_info(date(2027, 1, 31), 2)
        after = build_fiscal_year_info(date(2027, 2, 1), 2)
        self.assertEqual(before["current_fiscal_year"], 2026)
        self.assertEqual(before["retention_start_date"], "2023-02-01")
        self.assertEqual(after["current_fiscal_year"], 2027)
        self.assertEqual(after["retention_start_date"], "2024-02-01")

    def test_invalid_values_are_not_corrected(self):
        self.assertIsNone(parse_journal_date("20230230"))
        self.assertIsNone(parse_journal_date("not-a-date"))
        for month in (0, 13):
            with self.assertRaises(ValueError):
                validate_fiscal_year_start_month(month)

    def test_invalid_dates_stop_retention(self):
        for invalid_date in ("20260230", "", "abcd"):
            with self.subTest(invalid_date=invalid_date):
                with self.assertRaises(InvalidJournalDateError):
                    engine.keep_recent_years(
                        [{"伝票日付": invalid_date}],
                        today=date(2026, 8, 21),
                        start_month=2,
                    )

    def test_invalid_new_row_does_not_rewrite_transactions(self):
        valid_row = self._row("20260201")
        for invalid_date in ("20260230", "", "abcd"):
            with self.subTest(invalid_date=invalid_date):
                invalid_row = self._row(invalid_date)
                with tempfile.TemporaryDirectory() as temp_dir:
                    output_path = Path(temp_dir) / "transactions.csv"
                    self._write_rows(output_path, [valid_row])
                    before = output_path.read_bytes()

                    with patch.object(engine, "OUTPUT_PATH", str(output_path)):
                        with self.assertRaises(InvalidJournalDateError):
                            engine.update_search_csv([[invalid_row]])

                    self.assertEqual(output_path.read_bytes(), before)

    def test_invalid_existing_row_does_not_rewrite_transactions(self):
        valid_row = self._row("20260201")
        invalid_row = self._row("")
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "transactions.csv"
            self._write_rows(output_path, [invalid_row])
            before = output_path.read_bytes()

            with patch.object(engine, "OUTPUT_PATH", str(output_path)):
                with self.assertRaises(InvalidJournalDateError):
                    engine.append_to_csv([valid_row])

            self.assertEqual(output_path.read_bytes(), before)

    @staticmethod
    def _row(journal_date):
        row = {column: "" for column in EPSON_COLUMNS}
        row["伝票日付"] = journal_date
        row["借方金額"] = "1"
        row["貸方金額"] = "1"
        return row

    @staticmethod
    def _write_rows(path, rows):
        with path.open("w", encoding="utf-8-sig", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=EPSON_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)


if __name__ == "__main__":
    unittest.main()
