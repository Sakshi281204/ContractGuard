"""Tier 3 (data access): read a producer CSV file without ever crashing on bad rows."""
from __future__ import annotations

import csv
import hashlib
import io
from dataclasses import dataclass, field
from pathlib import Path


class SourceFileError(Exception):
    """The file cannot be processed at all (missing, not UTF-8, unusable header)."""


@dataclass(frozen=True)
class MalformedRow:
    line_number: int
    raw: list
    reason: str


@dataclass
class ReadResult:
    file_name: str
    sha256: str
    header: list
    rows: list = field(default_factory=list)          # one dict per well-formed record
    row_numbers: list = field(default_factory=list)   # source line number of each row
    malformed: list = field(default_factory=list)     # MalformedRow items
    blank_lines_skipped: int = 0

    @property
    def total_records(self) -> int:
        return len(self.rows) + len(self.malformed)


def read_csv_file(path) -> ReadResult:
    path = Path(path)
    if not path.is_file():
        raise SourceFileError(f"source file not found: {path}")
    data = path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    try:
        text = data.decode("utf-8-sig")  # also removes a UTF-8 byte-order mark
    except UnicodeDecodeError as exc:
        raise SourceFileError(f"{path.name}: not valid UTF-8 ({exc.reason})") from exc

    if text.strip() == "":
        return ReadResult(path.name, sha, [])

    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    header = None
    rows: list = []
    numbers: list = []
    malformed: list = []
    blanks = 0

    while True:
        start = reader.line_num + 1
        try:
            fields = next(reader)
        except StopIteration:
            break
        except csv.Error as exc:
            if header is None:
                raise SourceFileError(f"{path.name}: header row is malformed ({exc})") from exc
            malformed.append(MalformedRow(start, [], f"malformed CSV record ({exc})"))
            continue

        if not fields:
            blanks += 1
            continue
        if header is None:
            header = [h.strip() for h in fields]
            if any(h == "" for h in header):
                raise SourceFileError(f"{path.name}: header has an empty column name")
            if len(set(header)) != len(header):
                raise SourceFileError(f"{path.name}: header has duplicate column names")
            continue
        if len(fields) != len(header):
            malformed.append(MalformedRow(
                start, fields, f"expected {len(header)} fields, got {len(fields)}"
            ))
            continue
        rows.append(dict(zip(header, fields)))
        numbers.append(start)

    return ReadResult(path.name, sha, header or [], rows, numbers, malformed, blanks)
