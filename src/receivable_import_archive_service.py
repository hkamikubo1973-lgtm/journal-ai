"""Strict read and append model for cleaned receivable import identities."""

from __future__ import annotations

import io
from pathlib import Path

import pandas as pd

from receivable_duplicate_key_service import (
    BILLING_DUPLICATE_COLUMNS,
    receivable_duplicate_key,
)
from receivable_persistence_service import (
    IMPORT_ARCHIVE_FILENAME,
    ReceivableLedgerError,
    ReceivableLedgerMalformedError,
    ReceivableLedgerSchemaError,
)


ARCHIVE_COLUMNS = ["未収ID", *BILLING_DUPLICATE_COLUMNS]


def load_receivable_import_archive(
    receivables_directory: Path,
) -> tuple[pd.DataFrame, bytes | None]:
    """Missing means empty; malformed or incomplete archives fail closed."""

    path = Path(receivables_directory) / IMPORT_ARCHIVE_FILENAME
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return pd.DataFrame(columns=ARCHIVE_COLUMNS), None
    except OSError as error:
        raise ReceivableLedgerError("Could not read import archive") from error
    try:
        frame = pd.read_csv(
            io.BytesIO(raw), dtype=str, keep_default_na=False,
            encoding="utf-8-sig",
        )
    except (UnicodeError, ValueError, pd.errors.ParserError, pd.errors.EmptyDataError) as error:
        raise ReceivableLedgerMalformedError("Import archive is malformed") from error
    if any(column not in frame.columns for column in ARCHIVE_COLUMNS):
        raise ReceivableLedgerSchemaError("Import archive columns are missing")
    return frame, raw


def append_archive_rows(
    archive: pd.DataFrame,
    cleaned: pd.DataFrame,
) -> pd.DataFrame:
    """Keep existing row order and values; append first unseen six-key only."""

    keys = {
        receivable_duplicate_key(row)
        for _, row in archive.iterrows()
    }
    additions = []
    for _, row in cleaned.iterrows():
        key = receivable_duplicate_key(row)
        if key in keys:
            continue
        keys.add(key)
        additions.append({column: row.get(column, "") for column in archive.columns})
    if not additions:
        return archive.copy(deep=True)
    return pd.concat([archive, pd.DataFrame(additions, columns=archive.columns)], ignore_index=True)
