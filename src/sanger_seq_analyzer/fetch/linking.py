"""Link genotyping results to the lab's spreadsheets and build the MeaNS upload rows.

Matching chain (each step is reported so a wrong match can be spotted):

1. Genotyping table ``Accession`` = Masterlist ``ACCESSION``;
2. Masterlist ``MRN`` = Daily sheet ``MRN (reporting facility)``;
3. MOHAP line list ``LabID`` = Masterlist ``LAB ID``, else Daily sheet ``Lab ID #``,
   else the accession, else the Daily sheet ``Unique Identifier``.

Most MeaNS columns come from the line list, with the Masterlist and Daily sheet
as fall-backs; the WHO strain name is generated (see :mod:`.strains`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .strains import COUNTRY, Numbering, city_name
from .tables import Table, as_text, looks_like_accession, norm_key, parse_date
from .tables import compact as tables_compact


@dataclass(frozen=True)
class SourceSpec:
    key: str  # id used in settings and column prefixes
    title: str  # shown to the user
    prefix: str  # column prefix in the all-columns table
    match_column: str  # column every row needs
    expected: tuple[str, ...]  # column names used to find the header row
    combine: bool = True  # read every sheet that has the header


GENOTYPING = SourceSpec("genotyping", "Genotyping results", "Genotyping", "Accession",
                        ("SN", "Accession", "PCR Result", "ct value", "Genotyping Result", "COMMENTS", "FINAL"),
                        combine=False)
MASTERLIST = SourceSpec("masterlist", "Measles and Rubella Positive Masterlist", "Masterlist", "ACCESSION",
                        ("SL.NO", "DATE", "HAAD#", "MRN", "NATIONALITY", "NAME", "AGE", "SEX", "SOURCE", "ACCESSION",
                         "RESULTS", "CT VALUE", "COLLECTION DATE", "DATE PERFORMED", "FACILITY", "REGION",
                         "SENDOUT STATUS", "LAB ID", "DSID", "NAMED STRAIN", "Comments"))
DAILY = SourceSpec("daily", "Measles daily sheet", "Daily sheet", "MRN (reporting facility)",
                   ("Sr. #", "Patient name", "Gender", "Age", "Nationality", "EID", "Lab ID #", "Unique Identifier",
                    "Reporting facility", "REGION", "MRN (reporting facility)", "Date of sample collection",
                    "Date of sample received at SKMC Lab.", "Measles IgM result (First sample)",
                    "Type of sample used for PCR", "Measles PCR result", "Rubella PCR Result",
                    "Case classification (Based on Lab results)", "Comments"))
LINELIST = SourceSpec("linelist", "MOHAP line list", "Line list", "LabID",
                      ("Country", "Province/state", "District", "Village", "CASE_ID", "Age in years", "AgeinMonths",
                       "Sex", "Nationality", "Vaccination_Status", "DLASTVACC", "Travel_History", "DOT",
                       "Country of travel", "Fever", "DONSET", "DNOT", "DOI", "DSCOLL", "LabID", "TypeofSpec",
                       "SpecCond", "DateSPECREC", "Result_Measles_RT-PCR", "Measles_genotype", "Source_Infection",
                       "Final_Classification", "Final_Outcome", "Comments"))
SOURCES = (MASTERLIST, DAILY, LINELIST)

MEANS_COLUMNS = (
    "WHO Strain Name", "Case ID (MRN)", "Case Onset Date", "Admin Level 2", "Admin Level 1", "Import Status",
    "Exporting Country", "Recent International Travel", "Vaccinated", "Accession", "Collection Date",
    "Receiving Date", "Specimen Type", "Genotype", "TAT", "UPLOADED?", "LINELIST ID", "to upload?", "comments",
)
TEMPLATE = SourceSpec("template", "MeaNS upload template", "Template", "WHO Strain Name", MEANS_COLUMNS,
                      combine=False)

# MeaNS column -> sources tried in order: (source key, column name)
MEANS_SOURCES: dict[str, tuple[tuple[str, str], ...]] = {
    "Case ID (MRN)": (("masterlist", "MRN"), ("daily", "MRN (reporting facility)"), ("linelist", "CASE_ID")),
    "Case Onset Date": (("linelist", "DONSET"),),
    "Admin Level 2": (("linelist", "District"),),
    "Admin Level 1": (("linelist", "Province/state"), ("masterlist", "REGION"), ("daily", "REGION")),
    "Import Status": (("linelist", "Source_Infection"),),
    "Exporting Country": (("linelist", "Country of travel"),),
    "Recent International Travel": (("linelist", "Travel_History"),),
    "Vaccinated": (("linelist", "Vaccination_Status"),),
    "Accession": (("genotyping", "Accession"),),
    "Collection Date": (("linelist", "DSCOLL"), ("masterlist", "COLLECTION DATE"),
                        ("daily", "Date of sample collection")),
    "Receiving Date": (("linelist", "DateSPECREC"), ("daily", "Date of sample received at SKMC Lab.")),
    "Specimen Type": (("linelist", "TypeofSpec"), ("masterlist", "SOURCE"), ("daily", "Type of sample used for PCR")),
    "Genotype": (("genotyping", "Genotyping Result"), ("linelist", "Measles_genotype")),
    "LINELIST ID": (("linelist", "CASE_ID"), ("linelist", "LabID")),
    "comments": (("genotyping", "COMMENTS"),),
}
DATE_COLUMNS = {"Case Onset Date", "Collection Date", "Receiving Date"}
COMPUTED = {
    "WHO Strain Name": "Generated: virus/Admin Level 1.ARE/ISO week.year of the onset date (collection date if no "
                       "onset date), numbered after the names already in the MeaNS template",
    "TAT": "Days from the receiving date to today",
    "UPLOADED?": "No until the row is uploaded to MeaNS",
    "to upload?": "Yes when the genotyping row has a genotype and does not say FAIL",
}
PREFIX = {s.key: s.prefix for s in (GENOTYPING, *SOURCES)}


def _index(table: Table | None, column: str) -> tuple[dict[str, list[dict]], str | None]:
    if table is None:
        return {}, None
    col = table.column(column)
    index: dict[str, list[dict]] = {}
    if col is None:
        return index, None
    for row in table.rows:
        k = norm_key(row.get(col))
        if k:
            index.setdefault(k, []).append(row)
    return index, col


def _get(table: Table | None, row: dict | None, *names: str):
    if table is None or row is None:
        return None
    col = table.column(*names)
    return row.get(col) if col else None


@dataclass
class LinkedSample:
    accession: str
    rows: dict[str, dict | None]  # source key -> matched row (or None)
    notes: list[str] = field(default_factory=list)  # how rows were matched, and problems
    linelist_by: str = ""  # which value matched the line list LabID

    @property
    def found(self) -> dict[str, bool]:
        return {k: v is not None for k, v in self.rows.items()}

    def status(self) -> str:
        missing = [PREFIX[k] for k, v in self.rows.items() if k != "genotyping" and v is None]
        return "All files matched" if not missing else "Not found in: " + ", ".join(missing)


@dataclass
class Linked:
    samples: list[LinkedSample]
    tables: dict[str, Table | None]

    def all_columns(self) -> list[str]:
        cols = ["Accession", "Match"]
        for key in ("genotyping", "masterlist", "daily", "linelist"):
            t = self.tables.get(key)
            if t is not None:
                cols += [f"{PREFIX[key]} · {c}" for c in t.columns]
        return cols

    def merged(self, s: LinkedSample) -> dict:
        out = {"Accession": s.accession, "Match": s.status()}
        for key, row in s.rows.items():
            if row is None:
                continue
            for c, v in row.items():
                out[f"{PREFIX[key]} · {c}"] = v
        return out


def _pick_daily(rows: list[dict], daily: Table, lab_id, collected: date | None) -> tuple[dict, str]:
    """Several daily-sheet rows for one MRN: the one with the same lab ID, else the closest collection date."""
    if len(rows) == 1:
        return rows[0], ""
    lab_col = daily.column("Lab ID #", "Lab ID")
    if lab_id and lab_col:
        same = [r for r in rows if norm_key(r.get(lab_col)) == norm_key(lab_id)]
        if same:
            return same[0], f"{len(rows)} daily-sheet rows for this MRN; used the one with lab ID {as_text(lab_id)}"
    date_col = daily.column("Date of sample collection")
    if collected and date_col:
        dated = [(abs((d - collected).days), i) for i, r in enumerate(rows) if (d := parse_date(r.get(date_col)))]
        if dated:
            best = rows[min(dated)[1]]
            return best, f"{len(rows)} daily-sheet rows for this MRN; used the closest collection date"
    return rows[-1], f"{len(rows)} daily-sheet rows for this MRN; used the last one"


def link(genotyping: Table, masterlist: Table | None = None, daily: Table | None = None,
         linelist: Table | None = None) -> Linked:
    """Match every genotyping row to the other files."""
    tables = {"genotyping": genotyping, "masterlist": masterlist, "daily": daily, "linelist": linelist}
    acc_col = genotyping.column("Accession")
    by_acc, _ = _index(masterlist, "ACCESSION")
    by_mrn, _ = _index(daily, "MRN (reporting facility)")
    by_lab, _ = _index(linelist, "LabID")
    by_case, _ = _index(linelist, "CASE_ID")
    samples: list[LinkedSample] = []
    seen: set[str] = set()
    for grow in genotyping.rows:
        acc_raw = grow.get(acc_col)
        acc = as_text(acc_raw).strip()
        if not acc or not looks_like_accession(acc):  # blank, or a sub-header cell such as "MeV"
            continue
        s = LinkedSample(acc, {"genotyping": grow, "masterlist": None, "daily": None, "linelist": None})
        if norm_key(acc) in seen:
            s.notes.append("accession listed more than once in the genotyping table")
        seen.add(norm_key(acc))
        # 1. masterlist by accession
        mrows = by_acc.get(norm_key(acc), [])
        if masterlist is not None:
            if mrows:
                s.rows["masterlist"] = mrows[0]
                if len(mrows) > 1:
                    s.notes.append(f"{len(mrows)} masterlist rows for this accession; used the first")
            else:
                s.notes.append("accession not found in the masterlist")
        master = s.rows["masterlist"]
        mrn = _get(masterlist, master, "MRN")
        lab_id = _get(masterlist, master, "LAB ID", "LABID")
        collected = parse_date(_get(masterlist, master, "COLLECTION DATE"))
        # 2. daily sheet by MRN
        if daily is not None:
            if master is None:
                s.notes.append("daily sheet not searched: no masterlist row (MRN unknown)")
            elif not norm_key(mrn):
                s.notes.append("daily sheet not searched: the masterlist row has no MRN")
            else:
                drows = by_mrn.get(norm_key(mrn), [])
                if drows:
                    s.rows["daily"], note = _pick_daily(drows, daily, lab_id, collected)
                    if note:
                        s.notes.append(note)
                else:
                    s.notes.append(f"MRN {as_text(mrn)} not found in the daily sheet")
        drow = s.rows["daily"]
        # 3. line list by LabID
        if linelist is not None:
            candidates = [("masterlist LAB ID", lab_id), ("daily sheet Lab ID #", _get(daily, drow, "Lab ID #")),
                          ("accession", acc), ("daily sheet Unique Identifier",
                                               _get(daily, drow, "Unique Identifier"))]
            for label, value in candidates:
                rows = by_lab.get(norm_key(value), []) if norm_key(value) else []
                if rows:
                    s.rows["linelist"], s.linelist_by = rows[0], label
                    if len(rows) > 1:
                        s.notes.append(f"{len(rows)} line-list rows with LabID {as_text(value)}; used the first")
                    break
            if s.rows["linelist"] is None and norm_key(mrn):
                rows = by_case.get(norm_key(mrn), [])
                if rows:
                    s.rows["linelist"], s.linelist_by = rows[0], "MRN = CASE_ID"
            if s.rows["linelist"] is None:
                tried = [as_text(v) for _, v in candidates if norm_key(v)]
                s.notes.append("not found in the line list (LabID tried: " + (", ".join(tried) or "none") + ")")
            elif s.linelist_by != "masterlist LAB ID":
                s.notes.append(f"line list matched by {s.linelist_by}")
        samples.append(s)
    return Linked(samples, tables)


# --- MeaNS rows ---------------------------------------------------------------------------------------------


@dataclass
class MeansRow:
    sample: LinkedSample
    values: dict[str, object]  # MeaNS column -> value (dates are datetime.date)
    sources: dict[str, str] = field(default_factory=dict)  # column -> where the value came from
    problems: list[str] = field(default_factory=list)
    strain_manual: bool = False  # the user typed the strain name: keep it when names are regenerated
    include: bool = True  # save this row to the template
    failed: bool = False  # the genotyping row says FAIL, REINJECT or REPEAT

    @property
    def accession(self) -> str:
        return self.sample.accession

    @property
    def state(self) -> str:
        """'fail' (red), 'check' (yellow: a problem or note, or no genotype) or 'pass' (green)."""
        if self.failed:
            return "fail"
        if self.problems or self.sample.notes or self.values.get("to upload?") != "Yes":
            return "check"
        return "pass"


def _failed(comments, final) -> bool:
    text = f"{as_text(comments)} {as_text(final)}".upper()
    return "FAIL" in text or "REINJECT" in text or "REPEAT" in text


def _upload_flag(genotype, comments, final) -> str:
    if _failed(comments, final):
        return "No"
    return "Yes" if as_text(genotype) else "No"


def build_rows(linked: Linked, virus: str = "MVs", existing_names=(), today: date | None = None) -> list[MeansRow]:
    """MeaNS rows for every linked sample, with strain names for the rows to upload."""
    today = today or date.today()
    rows = []
    for s in linked.samples:
        values: dict[str, object] = {c: "" for c in MEANS_COLUMNS}
        sources: dict[str, str] = {}
        for col, options in MEANS_SOURCES.items():
            for key, name in options:
                table, row = linked.tables.get(key), s.rows.get(key)
                if table is None or row is None:
                    continue
                actual = table.column(name)
                value = row.get(actual) if actual else None
                if value is None or as_text(value) == "":
                    continue
                if col in DATE_COLUMNS:
                    day = parse_date(value)
                    if day is None:
                        continue
                    value = day
                values[col] = value if not isinstance(value, str) else value.strip()
                sources[col] = f"{PREFIX[key]}: {actual}"
                break
        received = values["Receiving Date"]
        if isinstance(received, date):
            values["TAT"] = (today - received).days
            sources["TAT"] = "Computed: days from the receiving date to today"
        values["UPLOADED?"] = "No"
        g = s.rows["genotyping"] or {}
        gt = linked.tables["genotyping"]
        comments, final = g.get(gt.column("COMMENTS") or "", ""), g.get(gt.column("FINAL") or "", "")
        values["to upload?"] = _upload_flag(values["Genotype"], comments, final)
        row = MeansRow(s, values, sources, include=values["to upload?"] == "Yes", failed=_failed(comments, final))
        rows.append(row)
    assign_strain_names(rows, virus, existing_names)
    return rows


def assign_strain_names(rows: list[MeansRow], virus: str, existing_names=()) -> Numbering:
    """(Re)generate WHO strain names for rows marked to upload, in onset-date order.

    Rows whose name was typed by the user keep it (and reserve its number). Problems
    (no Admin Level 1, no date, unknown city) are written to each row.
    """
    numbering = Numbering(existing_names)
    for r in rows:
        r.problems = [p for p in r.problems if not p.startswith("strain name:")]
        if r.strain_manual:
            from .strains import parse
            parsed = parse(r.values.get("WHO Strain Name"))
            if parsed is not None:
                if numbering.exists(parsed):
                    r.problems.append("strain name: this name is already in the MeaNS template")
                numbering.add(parsed)

    def order(r: MeansRow):
        d = r.values.get("Case Onset Date") or r.values.get("Collection Date")
        return (d if isinstance(d, date) else date.max, r.accession)

    for r in sorted(rows, key=order):
        if r.strain_manual:
            continue
        r.sources.pop("WHO Strain Name", None)
        if r.values.get("to upload?") != "Yes":
            r.values["WHO Strain Name"] = ""
            continue
        city, known = city_name(r.values.get("Admin Level 1"))
        day = r.values.get("Case Onset Date")
        used = "onset date"
        if not isinstance(day, date):
            day, used = r.values.get("Collection Date"), "collection date (no onset date)"
        if not city or not isinstance(day, date):
            missing = " and ".join(x for x, ok in (("Admin Level 1", city), ("onset/collection date",
                                                                                isinstance(day, date))) if not ok)
            r.values["WHO Strain Name"] = ""
            r.problems.append(f"strain name: needs {missing}")
            continue
        name = numbering.next_name(virus, city, day, COUNTRY)
        r.values["WHO Strain Name"] = str(name)
        r.sources["WHO Strain Name"] = f"Generated from Admin Level 1 and the {used}"
        if not known:
            r.problems.append(f"strain name: '{city}' is not a known emirate; check Admin Level 1")
        elif tables_compact(r.values.get("Admin Level 1")) != tables_compact(city):
            r.problems.append(f"strain name: Admin Level 1 '{r.values.get('Admin Level 1')}' written as {city}")
    return numbering


def genotyping_from_run(run, reviews: dict | None = None) -> Table:
    """A genotyping table from a QC run: one row per sample, COMMENTS = PASS or FAIL REINJECT ...

    Genotype, PCR result and Ct value are left blank for the user to fill in.
    """
    from ..review import effective_status

    reviews = reviews or {}
    rows = []
    for i, sr in enumerate(run.samples, 1):
        c = sr.consensus
        status = effective_status(sr, reviews)
        if status == "PASS":
            comment = "PASS"
        else:
            bad = [d for d, read in (("FORWARD", c.forward), ("REVERSE", c.reverse)) if read is None or not read.passed]
            if len(bad) == 2:
                comment = "FAIL REINJECT BOTH STRANDS"
            elif bad:
                comment = f"FAIL REINJECT {bad[0]} STRAND"
            else:
                comment = "FAIL" + (f" ({c.reasons[0]})" if c.reasons else "")
        rows.append({"SN": i, "Accession": sr.sample, "PCR Result": "", "ct value": "", "Genotyping Result": "",
                     "COMMENTS": comment, "FINAL": ""})
    return Table(GENOTYPING.title, list(GENOTYPING.expected), rows)
