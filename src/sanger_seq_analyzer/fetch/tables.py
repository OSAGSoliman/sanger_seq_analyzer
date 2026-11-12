"""Reading spreadsheets and pasted tables into plain rows.

Lab spreadsheets rarely start at cell A1: there are titles above the header,
merged cells, several sheets, extra spaces and line breaks in column names. So
the header row is found by looking for the row that contains most of the
expected column names, and names are compared after removing case, spaces and
punctuation ("Measles IgM result   (First sample)" = "measles igm result (first
sample)").
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

HEADER_SEARCH_ROWS = 40  # header must be within the first rows of a sheet
EXCEL_SUFFIXES = (".xlsx", ".xlsm")  # read with openpyxl (and the only formats the template can be written in)
BINARY_SUFFIXES = (".xlsb", ".xls", ".ods")  # Excel Binary Workbook, Excel 97-2003, OpenDocument: python-calamine
TEXT_SUFFIXES = (".csv", ".tsv", ".txt")
READABLE_SUFFIXES = EXCEL_SUFFIXES + BINARY_SUFFIXES + TEXT_SUFFIXES


class TableError(ValueError):
    """The file cannot be used; the message says why and what to do."""


def compact(text) -> str:
    """Lower-case letters and digits only: the form used to compare column names."""
    return re.sub(r"[^0-9a-z]", "", str(text or "").casefold())


def clean_header(text) -> str:
    """A column name as shown: single spaces, no line breaks."""
    return re.sub(r"\s+", " ", str(text or "")).strip()


def looks_like_accession(value) -> bool:
    """True for an accession number; False for blanks, sub-header cells such as "MeV" and repeated headers."""
    return any(ch.isdigit() for ch in as_text(value))


def norm_key(value) -> str:
    """A cell value as a matching key: upper case, letters and digits only, no leading zeros on numbers.

    ``12345.0`` (Excel number), ``"012345"`` and ``" 12345 "`` all become ``"12345"``.
    """
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y%m%d")
    text = re.sub(r"[^0-9A-Z]", "", str(value).upper())
    if text.isdigit():
        text = text.lstrip("0") or "0"
    return text


def as_text(value) -> str:
    """A cell value for display: dates as dd/mm/yyyy, whole numbers without .0."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        if value.hour or value.minute or value.second:
            return value.strftime("%d/%m/%Y %H:%M")
        return value.strftime("%d/%m/%Y")
    if isinstance(value, date):
        return value.strftime("%d/%m/%Y")
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else f"{value:g}"
    return str(value).strip()


_DATE_FORMATS = ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y-%m-%d", "%Y/%m/%d", "%d/%m/%y", "%d-%m-%y",
                 "%d-%b-%Y", "%d %b %Y", "%d-%b-%y", "%d %B %Y", "%d-%B-%Y", "%b %d, %Y", "%Y%m%d")


def parse_date(value) -> date | None:
    """A date from an Excel date, an Excel serial number or day-first text; None when it is not a date."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if 20000 <= value <= 80000:  # Excel serial day (1954-2119)
            return (datetime(1899, 12, 30) + timedelta(days=float(value))).date()
        return None
    text = str(value).strip()
    text = re.split(r"[ T]\d{1,2}:\d{2}", text)[0].strip()  # drop a time part
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def match_column(wanted: str, columns: list[str]) -> str | None:
    """The column of ``columns`` that is ``wanted``: same compact name, else the closest prefix match.

    "Province/state" finds "Province / State"; "MeaslesIgM (pending refers to not tested)" finds
    "MeaslesIgM". Prefix matches need at least 5 letters in common.
    """
    w = compact(wanted)
    if not w:
        return None
    for c in columns:
        if compact(c) == w:
            return c
    best, best_gap = None, None
    for c in columns:
        cc = compact(c)
        if len(cc) >= 5 and len(w) >= 5 and (cc.startswith(w) or w.startswith(cc)):
            gap = abs(len(cc) - len(w))
            if best_gap is None or gap < best_gap:
                best, best_gap = c, gap
    return best


@dataclass
class Table:
    """Rows of one source, each a dict keyed by the (cleaned) column names."""

    name: str
    columns: list[str]
    rows: list[dict]
    path: Path | None = None
    sheets: list[str] = field(default_factory=list)  # sheets that were read
    skipped_sheets: list[str] = field(default_factory=list)  # sheets without the expected header
    warnings: list[str] = field(default_factory=list)

    def column(self, *names: str) -> str | None:
        """The first of ``names`` that this table has (fuzzy, see :func:`match_column`)."""
        for n in names:
            found = match_column(n, self.columns)
            if found:
                return found
        return None

    def summary(self) -> str:
        sheets = f" from {len(self.sheets)} sheets" if len(self.sheets) > 1 else ""
        return f"{len(self.rows):,} rows{sheets}"


def _unique(names: list[str]) -> list[str]:
    """Header names with blanks named and repeats numbered: "Date", "Date (2)"."""
    out, seen = [], {}
    for i, n in enumerate(names):
        n = clean_header(n) or f"Column {i + 1}"
        k = n.casefold()
        if k in seen:
            seen[k] += 1
            n = f"{n} ({seen[k]})"
        else:
            seen[k] = 1
        out.append(n)
    return out


def _score(row, expected_compact: list[str]) -> int:
    cells = [clean_header(c) for c in row if c not in (None, "")]
    return sum(1 for e in expected_compact if match_column(e, cells))


def find_header(rows: list[tuple], expected: list[str]) -> tuple[int, int]:
    """(index, score) of the row among the first ones that contains most expected column names."""
    best, best_score = 0, -1
    for i, row in enumerate(rows[:HEADER_SEARCH_ROWS]):
        s = _score(row, expected)
        if s > best_score:
            best, best_score = i, s
    return best, max(best_score, 0)


def _rows_to_records(rows: list[tuple], header_index: int) -> tuple[list[str], list[dict]]:
    header = list(rows[header_index])
    # drop trailing columns that have neither a name nor data
    width = len(header)
    columns = _unique(header)
    records = []
    for row in rows[header_index + 1:]:
        values = list(row)[:width] + [None] * max(0, width - len(row))
        if all(v is None or (isinstance(v, str) and not v.strip()) for v in values):
            continue
        records.append({c: v for c, v in zip(columns, values) if not (c.startswith("Column ") and v is None)})
    named = [c for c in columns if not c.startswith("Column ") or any(r.get(c) not in (None, "") for r in records)]
    return named, records


def _excel_sheets(path: Path) -> list[tuple[str, list[tuple]]]:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise TableError("reading Excel files needs the openpyxl package (pip install openpyxl)") from exc
    try:
        wb = load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:  # zip errors, bad XML, password-protected files
        raise TableError(f"{path.name} could not be opened as an Excel workbook ({type(exc).__name__}: {exc}). "
                         "If it is an old .xls file, save it as .xlsx first.") from exc
    try:
        out = []
        for ws in wb.worksheets:
            rows = []
            for row in ws.iter_rows(values_only=True):
                rows.append(tuple(row))
            while rows and all(v is None for v in rows[-1]):
                rows.pop()
            out.append((ws.title, rows))
        return out
    finally:
        wb.close()


def _calamine_sheets(path: Path) -> list[tuple[str, list[tuple]]]:
    """Sheets of an .xlsb, .xls or .ods file (dates come back as dates)."""
    try:
        from python_calamine import CalamineWorkbook
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise TableError(f"reading {path.suffix} files needs the python-calamine package "
                         "(pip install python-calamine)") from exc
    try:
        wb = CalamineWorkbook.from_path(str(path))
    except Exception as exc:
        name = type(exc).__name__
        if "Password" in name:
            raise TableError(f"{path.name} is password protected: save a copy without the password") from exc
        raise TableError(f"{path.name} could not be opened ({name}: {exc})") from exc
    out = []
    for title in wb.sheet_names:
        try:
            rows = wb.get_sheet_by_name(title).to_python(skip_empty_area=False)
        except Exception:  # chart sheets and other sheets without cells
            continue
        rows = [tuple(None if v == "" else v for v in row) for row in rows]
        while rows and all(v is None for v in rows[-1]):
            rows.pop()
        out.append((title, rows))
    return out


def parse_text(text: str) -> list[tuple]:
    """Rows of pasted or CSV/TSV text (tab-separated when there are tabs, else comma)."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    sample = "\n".join(text.split("\n")[:20])
    delimiter = "\t" if "\t" in sample else ("," if "," in sample else "\t")
    return [tuple(c if c.strip() else None for c in row) for row in csv.reader(io.StringIO(text), delimiter=delimiter)]


def _text_sheets(path: Path) -> list[tuple[str, list[tuple]]]:
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = path.read_text(encoding=encoding)
            break
        except UnicodeDecodeError:
            continue
    return [(path.stem, parse_text(text))]


def read_sheets(path: str | Path) -> list[tuple[str, list[tuple]]]:
    path = Path(path)
    if not path.is_file():
        raise TableError(f"file not found: {path}")
    suffix = path.suffix.lower()
    if suffix in EXCEL_SUFFIXES:
        return _excel_sheets(path)
    if suffix in BINARY_SUFFIXES:
        return _calamine_sheets(path)
    if suffix in TEXT_SUFFIXES:
        return _text_sheets(path)
    raise TableError(f"{path.name}: use an Excel file (.xlsx, .xlsm, .xlsb, .xls), .ods or .csv")


def table_from_rows(name: str, sheets: list[tuple[str, list[tuple]]], expected: list[str], key: str,
                    combine: bool = False, path: Path | None = None, min_score: int = 2,
                    key_filter=None) -> Table:
    """Find the header in each sheet and build a :class:`Table`.

    ``key`` is the column every row must have (rows without it are dropped, and so are
    rows for which ``key_filter(value)`` is False). With ``combine`` every sheet that has
    the header is read and a "Sheet" column added; otherwise only the best sheet is used.
    """
    found = []
    for title, rows in sheets:
        if not rows:
            continue
        index, score = find_header(rows, expected)
        header_cells = [clean_header(c) for c in rows[index] if c not in (None, "")]
        has_key = match_column(key, header_cells) is not None
        found.append((score + (5 if has_key else 0), has_key, title, rows, index))
    need = max(min_score, len(expected) // 4)  # a quarter of the expected columns, so a wrong file is refused
    usable = [f for f in found if f[1] and f[0] - 5 >= min(need, len(expected))]
    if not usable:
        names = ", ".join(t for _, _, t, _, _ in found) or "none"
        weak = [f for f in found if f[1]]
        if weak:
            best = max(weak, key=lambda f: f[0])
            cols = ", ".join(clean_header(c) for c in best[3][best[4]] if c not in (None, ""))
            raise TableError(f"sheet '{best[2]}' has a '{key}' column but only {best[0] - 5} of the expected "
                             f"{name} columns (found: {cols[:300]}). Check that this is the {name} file.")
        raise TableError(f"no sheet has a header with a '{key}' column (sheets: {names}). Check that this is the "
                         f"{name} file.")
    usable.sort(key=lambda f: -f[0])
    chosen = usable if combine else usable[:1]
    if combine:  # drop sheets that match much worse than the best one (notes, pivot tables)
        top = chosen[0][0]
        chosen = [f for f in chosen if f[0] >= max(6, top * 0.5)]
        chosen.sort(key=lambda f: [t for _, _, t, _, _ in found].index(f[2]))
    columns: list[str] = []
    by_compact: dict[str, str] = {}
    records: list[dict] = []
    for _score_, _k, title, rows, index in chosen:
        cols, recs = _rows_to_records(rows, index)
        # the same column in another sheet may be spelled with other spaces or case: use the first spelling
        rename = {}
        for c in cols:
            k = compact(c) or c
            if k not in by_compact:
                by_compact[k] = c
                columns.append(c)
            rename[c] = by_compact[k]
        key_col = match_column(key, cols)
        for r in recs:
            if norm_key(r.get(key_col)) == "" or (key_filter is not None and not key_filter(r.get(key_col))):
                continue
            r = {rename.get(c, c): v for c, v in r.items()}
            if combine:
                r = {"Sheet": title, **r}
            records.append(r)
    if combine:
        columns = ["Sheet", *columns]
    skipped = [t for _, _, t, _, _ in found if t not in {f[2] for f in chosen}]
    table = Table(name, columns, records, path, [f[2] for f in chosen], skipped)
    if not records:
        table.warnings.append(f"{name}: the header was found but no rows have a {key}")
    return table


def read_table(path: str | Path, name: str, expected: list[str], key: str, combine: bool = False,
               key_filter=None) -> Table:
    path = Path(path)
    return table_from_rows(name, read_sheets(path), expected, key, combine=combine, path=path,
                           key_filter=key_filter)


def text_table(text: str, name: str, expected: list[str], key: str, key_filter=None) -> Table:
    """A table from text pasted from Excel (tab-separated) or CSV."""
    return table_from_rows(name, [("Pasted", parse_text(text))], expected, key, min_score=1, key_filter=key_filter)
