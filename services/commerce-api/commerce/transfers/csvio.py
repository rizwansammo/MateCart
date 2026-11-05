"""CSV and JSON Lines reading and writing for imports and exports (D-105).

Reading: UTF-8 (a byte-order mark is accepted, so spreadsheet "CSV UTF-8" files work), bounded
size, rows and columns; the header row is required. Writing: every text cell that a spreadsheet
could treat as a formula (starting with ``=``, ``+``, ``-``, ``@``, tab or carriage return) is
prefixed with an apostrophe (CSV injection, OWASP), numbers excepted.
"""

from __future__ import annotations

import csv
import io
import json
import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from typing import Any

from commerce.core.domain import DomainError

MAX_ROWS = 50_000
MAX_COLUMNS = 80
MAX_CELL = 20_000
_NUMBER = re.compile(r"^-?\d+(\.\d+)?$")
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


class FileRefused(DomainError):
    default_code = "import_file_invalid"


def decode(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise FileRefused(
            "Save the file as UTF-8 (in Excel: CSV UTF-8) and upload it again.",
            "import_encoding",
        ) from None


def normal_header(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")


def read_csv(data: bytes) -> tuple[list[str], list[dict[str, str]]]:
    text = decode(data)
    csv.field_size_limit(MAX_CELL * 2)
    try:
        reader = csv.reader(io.StringIO(text, newline=""))
        rows = iter(reader)
        header = next(rows, None)
        if not header or not any(cell.strip() for cell in header):
            raise FileRefused("The file needs a header row.", "import_no_header")
        if len(header) > MAX_COLUMNS:
            raise FileRefused(f"Use at most {MAX_COLUMNS} columns.", "import_too_many_columns")
        names = [cell.strip()[:80] for cell in header]
        if len(set(names)) != len(names):
            raise FileRefused("Column names must be unique.", "import_duplicate_columns")
        records: list[dict[str, str]] = []
        for index, row in enumerate(rows):
            if index >= MAX_ROWS:
                raise FileRefused(f"Use at most {MAX_ROWS} rows per file.", "import_too_many_rows")
            if not any(cell.strip() for cell in row):
                continue
            records.append({name: (row[i] if i < len(row) else "") for i, name in enumerate(names)})
    except csv.Error:
        raise FileRefused("The file is not valid CSV.", "import_csv_invalid") from None
    return names, records


def read_jsonl(data: bytes) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for index, line in enumerate(decode(data).splitlines()):
        if not line.strip():
            continue
        if len(records) >= MAX_ROWS:
            raise FileRefused(f"Use at most {MAX_ROWS} records per file.", "import_too_many_rows")
        try:
            value = json.loads(line)
        except ValueError:
            raise FileRefused(
                f"Line {index + 1} is not valid JSON.", "import_jsonl_invalid"
            ) from None
        if not isinstance(value, dict):
            raise FileRefused(f"Line {index + 1} is not an object.", "import_jsonl_invalid")
        records.append(value)
    return records


def safe_cell(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    if text.startswith(_FORMULA_START) and not _NUMBER.match(text):
        return "'" + text
    return text


def write_csv(header: Sequence[str], rows: Iterable[Mapping[str, Any]]) -> Iterator[str]:
    """CSV text in chunks (header first), every cell made formula-safe."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow([safe_cell(name) for name in header])
    yield buffer.getvalue()
    for row in rows:
        buffer.seek(0)
        buffer.truncate()
        writer.writerow([safe_cell(row.get(name)) for name in header])
        yield buffer.getvalue()


def write_jsonl(rows: Iterable[Mapping[str, Any]]) -> Iterator[str]:
    for row in rows:
        yield json.dumps(row, ensure_ascii=False, default=str) + "\n"
