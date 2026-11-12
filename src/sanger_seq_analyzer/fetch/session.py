"""One user's Fetch data work: source tables, the genotyping table, linked MeaNS rows, saving.

The desktop app wraps this in a Qt controller (signals, a worker thread); the server keeps
one per signed-in user. Nothing here depends on Qt.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from . import linking, sources, tables, template
from .linking import DATE_COLUMNS, GENOTYPING, MEANS_COLUMNS

# MeaNS columns that the strain name is built from: editing one renumbers the names
NAME_INPUTS = {"Admin Level 1", "Case Onset Date", "Collection Date", "to upload?"}


class FetchSession:
    def __init__(self, virus: str = "MVs", columns: list[str] | None = None):
        self.paths: dict[str, str] = {k: "" for k in sources.SPECS}
        self.tables: dict[str, tables.Table | None] = {k: None for k in sources.SPECS}
        self.errors: dict[str, str] = {k: "" for k in sources.SPECS}
        self.stamps: dict[str, tuple] = {}
        self.virus = virus if virus in ("MVs", "RVs") else "MVs"
        self.columns: list[str] = list(columns or MEANS_COLUMNS)
        self.genotyping: tables.Table | None = None
        self.genotyping_error = ""
        self.linked: linking.Linked | None = None
        self.rows: list[linking.MeansRow] = []

    # --- source files ----------------------------------------------------------------------------------

    def missing_paths(self) -> list[str]:
        return [k for k in sources.SPECS if not self.paths.get(k)]

    def set_path(self, key: str, path: str) -> bool:
        """Choose the file for source ``key``; returns False when it is the same file."""
        path = (path or "").strip()
        if path == self.paths.get(key):
            return False
        self.paths[key] = path
        self.tables[key], self.errors[key] = None, ""
        self.stamps.pop(key, None)
        return True

    def read_changed(self) -> dict[str, tuple]:
        """Read every source whose file changed: key -> (stamp, table or None, error). Safe off the main thread."""
        out = {}
        for key in sources.SPECS:
            path = self.paths.get(key)
            if not path:
                continue
            st = sources.stamp(path)
            if st is not None and st == self.stamps.get(key) and self.tables.get(key) is not None:
                continue
            out[key] = sources.read_one(key, path)
        return out

    def apply(self, results: dict[str, tuple]) -> None:
        for key, (st, table, error) in results.items():
            self.set_table(key, table, error, st)

    def set_table(self, key: str, table: tables.Table | None, error: str = "", stamp: tuple | None = None) -> None:
        self.tables[key], self.errors[key] = table, error
        if stamp is not None and table is not None:
            self.stamps[key] = stamp
        else:
            self.stamps.pop(key, None)

    def load_sources(self) -> None:
        self.apply(self.read_changed())

    def existing_names(self) -> list:
        return sources.strain_names(self.tables.get("template"))

    # --- genotyping table ------------------------------------------------------------------------------

    def set_genotyping_text(self, text: str) -> bool:
        try:
            self.genotyping = tables.text_table(text, GENOTYPING.title, list(GENOTYPING.expected),
                                                GENOTYPING.match_column, key_filter=tables.looks_like_accession)
            self.genotyping_error = ""
            return True
        except tables.TableError as exc:
            self.genotyping_error = f"{exc}. Copy the table from Excel including its header row (SN, Accession, ...)."
            return False

    def set_genotyping_file(self, path: str | Path) -> bool:
        try:
            self.genotyping = tables.read_table(path, GENOTYPING.title, list(GENOTYPING.expected),
                                                GENOTYPING.match_column, key_filter=tables.looks_like_accession)
            self.genotyping_error = ""
            return True
        except tables.TableError as exc:
            self.genotyping_error = str(exc)
            return False

    def set_genotyping_rows(self, columns: list[str], rows: list[dict]) -> None:
        """The table as edited by the user."""
        acc = tables.match_column("Accession", columns)
        self.genotyping = tables.Table(GENOTYPING.title, columns,
                                       [r for r in rows if tables.looks_like_accession(r.get(acc))])
        self.genotyping_error = ""

    def genotyping_from_run(self, run, reviews) -> None:
        self.genotyping = linking.genotyping_from_run(run, reviews)
        self.genotyping_error = ""

    def clear_genotyping(self) -> None:
        self.genotyping, self.genotyping_error = None, ""

    # --- linking ---------------------------------------------------------------------------------------

    def set_virus(self, virus: str) -> bool:
        """Switch between measles (MVs) and rubella (RVs) names; returns True when it changed."""
        if virus not in ("MVs", "RVs") or virus == self.virus:
            return False
        self.virus = virus
        if self.rows:
            self.regenerate_names()
        return True

    def fetch(self, today: date | None = None) -> str:
        """Link the genotyping table to the loaded sources; returns a problem to show, or "".

        ``today`` (for TAT) defaults to the computer's date; the server passes the lab's local date.
        """
        if self.genotyping is None or not self.genotyping.rows:
            return "Paste or load the genotyping table first."
        if self.tables.get("masterlist") is None:
            return "The masterlist is needed to find each patient (choose it under Source files)."
        self.linked = linking.link(self.genotyping, self.tables.get("masterlist"), self.tables.get("daily"),
                                   self.tables.get("linelist"))
        self.rows = linking.build_rows(self.linked, self.virus, self.existing_names(), today=today)
        return ""

    def regenerate_names(self) -> None:
        linking.assign_strain_names(self.rows, self.virus, self.existing_names())

    def all_columns(self) -> list[str]:
        return self.linked.all_columns() if self.linked else []

    def set_columns(self, columns: list[str]) -> None:
        self.columns = list(columns) or list(MEANS_COLUMNS)

    def value(self, row: linking.MeansRow, column: str):
        if column in row.values:
            return row.values[column]
        if self.linked is None:
            return None
        return self.linked.merged(row.sample).get(column)

    def set_include(self, index: int, on: bool) -> None:
        self.rows[index].include = bool(on)

    def edit_cell(self, index: int, column: str, text: str) -> bool:
        """Store a value typed into the MeaNS table; returns True when the strain names were renumbered."""
        row = self.rows[index]
        if column not in MEANS_COLUMNS:
            return False
        text = (text or "").strip()
        if column in DATE_COLUMNS:
            row.values[column] = tables.parse_date(text) or text
        elif column == "TAT":
            row.values[column] = int(text) if text.lstrip("-").isdigit() else text
        else:
            row.values[column] = text
        if column == "WHO Strain Name":
            row.strain_manual = bool(text)
        if column == "to upload?":
            row.include = text.lower() in ("yes", "y")
        if column in NAME_INPUTS or column == "WHO Strain Name":
            self.regenerate_names()
            return True
        return False

    # --- saving ----------------------------------------------------------------------------------------

    def rows_to_save(self) -> list[linking.MeansRow]:
        return [r for r in self.rows if r.include]

    def template_rows(self) -> tuple[list[linking.MeansRow], list[dict]]:
        """The ticked rows and their MeaNS values; raises TableError when they cannot be saved."""
        rows = self.rows_to_save()
        if not rows:
            raise tables.TableError("no rows are ticked to save")
        missing = [r.accession for r in rows if not str(r.values.get("WHO Strain Name") or "").strip()]
        if missing:
            raise tables.TableError("these rows have no WHO strain name: " + ", ".join(missing))
        return rows, [{c: r.values.get(c, "") for c in MEANS_COLUMNS} for r in rows]

    def mark_saved(self, rows: list[linking.MeansRow], result: template.AppendResult) -> None:
        """Untick the rows written to the template and keep their names if names are renumbered later."""
        saved = {tables.norm_key(r.values.get("Accession")) for r in rows} if result.written else set()
        skipped = {s.split(":", 1)[0] for s in result.skipped}
        for r in rows:
            if tables.norm_key(r.values.get("Accession")) in saved and r.accession not in skipped:
                r.include = False
                r.strain_manual = True

    def save_to_template(self, path: str | Path | None = None) -> template.AppendResult:
        """Append the ticked rows (MeaNS columns) to the template; raises TableError."""
        path = path or self.paths.get("template")
        if not path:
            raise tables.TableError("choose the MeaNS upload template under Source files first")
        rows, values = self.template_rows()
        result = template.append_rows(path, values, list(MEANS_COLUMNS))
        self.mark_saved(rows, result)
        if self.paths.get("template"):
            self.stamps.pop("template", None)
            self.load_sources()  # the next names continue after the ones just saved
        return result

    def workbook_sheets(self) -> list[tuple[str, list[str], list[list]]]:
        cols = list(self.columns)
        sheets = [("MeaNS", cols, [[self.value(r, c) for c in cols] for r in self.rows])]
        if self.linked is not None:
            all_cols = self.linked.all_columns()
            sheets.append(("All columns", all_cols,
                           [[self.linked.merged(s).get(c) for c in all_cols] for s in self.linked.samples]))
            sheets.append(("Matching", ["Accession", "Result", "Notes"],
                           [[s.accession, s.status(), "; ".join(s.notes)] for s in self.linked.samples]))
        return sheets

    def export_workbook(self, path: str | Path) -> Path:
        return template.write_workbook(path, self.workbook_sheets())
