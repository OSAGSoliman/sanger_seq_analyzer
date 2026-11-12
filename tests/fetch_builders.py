"""Synthetic lab spreadsheets for the Fetch data tests (no real patient data).

Each file copies the awkward parts of the real ones: a title above the header,
headers with line breaks and repeated names, several sheets, a data-dictionary
sheet, blank rows and a mistyped strain name.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from openpyxl import Workbook

GENOTYPING_TSV = (
    "SN\tAccession\tPCR Result\tct value\tGenotyping Result\tCOMMENTS\tFINAL\n"
    "\tMeV\t\t\t\t\t\n"  # the lab's sub-header row: "MeV" under Accession
    "\t\tMeV\t\t\t\t\n"
    "1\t02SV269990001\tPOSITIVE (WILD TYPE)\t23.74\t\tFAIL REINJECT FORWARD STRAND\t\n"
    "2\t02SV269990002\tPOSITIVE (WILD TYPE)\t19.69\t\tFAIL REINJECT BOTH STRANDS\t\n"
    "3\t02SV269990003\tPOSITIVE (WILD TYPE)\t23.91\tD8\tPASS\t\n"
    "4\t02SV269990004\tPOSITIVE (WILD TYPE)\t22.66\tD8\tPASS\t\n"
    "5\t02SV269990005\tPOSITIVE (WILD TYPE)\t26.46\tB3\tPASS\t\n"
    "6\t02SV269990006\tPOSITIVE (WILD TYPE)\t23.44\tD8\tPASS\t\n"
)

MASTER_HEADER = ["SL.NO", "DATE", "HAAD#", "MRN", "NATIONALITY", "NAME", "AGE", "SEX", "SOURCE", "ACCESSION",
                 "RESULTS", "CT VALUE", "COLLECTION DATE", "DATE PERFORMED", "FACILITY", "REGION", "SENDOUT STATUS",
                 "Sequenced by Oman", "Sequenced by RLID", "RLID Results", "LAB ID", "Uploaded to MeaNS?", "DSID",
                 "NAMED STRAIN", "Comments"]

DAILY_HEADER = ["Sr. #", "Patient name", "Gender", "Age", "Nationality", "EID", "Lab ID # ", "Unique Identifier",
                "Reporting facility", "REGION", "MRN (reporting facility)", "Type of sample used for serology test",
                "Date of sample collection", "Date of sample received at SKMC Lab.",
                "Measles IgM result                (First sample)", "Type of sample used for PCR",
                "Date of sample collection", "Measles PCR result                          ", "Rubella PCR Result",
                "Case classification           (Based on Lab results)", "Comments"]

LINE_HEADER = ["Country", "Province/state", "District", "Village", "CASE_ID", "Age in years", "AgeinMonths", "Sex",
               "Nationality", "Vaccination_Status", "DLASTVACC", "Travel_History", "DOT", "Country of travel",
               "Fever", "Maccopopular rash", "Cough", "DONSET", "DNOT", "DOI", "DSCOLL", "LabID", "TypeofSpec",
               "SpecCond", "DateSPECREC", "MeaslesIgM", "Result_Measles_RT-PCR ", "Measles_genotype",
               "Source_Infection", "Final_Classification", "Final_Outcome", "Comments"]

TEMPLATE_NAMES = ["MVs/Dubai.ARE/1.26/2", "MVs/Abu Dhabi.ARE/1.26", None, "MVs/Dubai.ARE/11.26",
                  "MVs/Dubai.ARE/11.26.7", "MVs/Dubai.ARE/11.26/4 ", "MVs/Sharjah.ARE/3.26", None]

# accession -> (MRN, lab id, region, line-list province, onset, collection, received, travel, country)
PATIENTS = {
    "02SV269990001": ("100001", "L-5001", "DUBAI", "Dubai", datetime(2026, 3, 9), datetime(2026, 3, 12),
                      datetime(2026, 3, 13), "No", ""),
    "02SV269990002": ("100002", "L-5002", "SHARJAH", "Sharjah", datetime(2026, 3, 10), datetime(2026, 3, 12),
                      datetime(2026, 3, 13), "No", ""),
    # Dubai, ISO week 11 of 2026 (9-15 March): the template already has numbers 1, 4 and 7
    "02SV269990003": ("100003", "L-5003", "DUBAI", "Dubai", datetime(2026, 3, 11), datetime(2026, 3, 14),
                      datetime(2026, 3, 15), "Yes", "India"),
    "02SV269990004": ("100004", "L-5004", "DUBAI", "Dubai", datetime(2026, 3, 12), datetime(2026, 3, 15),
                      datetime(2026, 3, 16), "No", ""),
    # Al Ain is named Abu Dhabi in strain names; week 1 of 2026 has "Abu Dhabi.ARE/1.26" already
    "02SV269990005": ("100005", "L-5005", "AL AIN", "Al Ain", datetime(2026, 1, 1), datetime(2026, 1, 4),
                      datetime(2026, 1, 5), "No", ""),
    # in the masterlist only: not in the daily sheet nor the line list
    "02SV269990006": ("100006", "L-5006", "AJMAN", None, None, datetime(2026, 3, 20), None, None, None),
}


def _sheet(ws, header, rows, title=None):
    if title:
        ws.append([title])
        ws.append([])
    ws.append(header)
    for r in rows:
        ws.append(r)


def build(folder: Path) -> dict[str, Path]:
    folder.mkdir(parents=True, exist_ok=True)
    paths = {}

    wb = Workbook()
    ws = wb.active
    ws.title = "2026"
    rows = []
    for i, (acc, p) in enumerate(PATIENTS.items(), 1):
        mrn, lab, region = p[0], p[1], p[2]
        rows.append([i, datetime(2026, 3, 15), f"H{i}", int(mrn), "Test", f"Test Patient {i}", 5, "M", "Throat swab",
                     acc, "POSITIVE", 22.5, p[5], datetime(2026, 3, 16), "Test Hospital", region, "", "", "", "",
                     lab, "", "", "", ""])
    _sheet(ws, MASTER_HEADER, rows, title="Measles and Rubella Positive Masterlist 2026")
    paths["masterlist"] = folder / "Measles and Rubella Positive Masterlist 2026.xlsx"
    wb.save(paths["masterlist"])

    wb = Workbook()
    jan, mar = wb.active, wb.create_sheet("March")
    jan.title = "January"
    notes = wb.create_sheet("Notes")
    notes.append(["This sheet explains the codes"])
    by_sheet = {"January": [], "March": []}
    for i, (acc, p) in enumerate(PATIENTS.items(), 1):
        if p[3] is None:
            continue
        mrn, lab, region = p[0], p[1], p[2]
        sheet = "January" if p[5].month == 1 else "March"
        by_sheet[sheet].append([i, f"Test Patient {i}", "M", 5, "Test", "", lab, f"U{i}", "Test Hospital", region,
                                f"0{mrn}", "Serum", p[5], p[6], "Positive", "Throat swab", p[5], "Positive",
                                "Negative", "Confirmed", ""])
    # a second, older row for patient 3 with another lab id: the matching lab id must win
    by_sheet["January"].append([99, "Test Patient 3", "M", 5, "Test", "", "L-0999", "U99", "Test Hospital", "DUBAI",
                                "100003", "Serum", datetime(2026, 1, 2), datetime(2026, 1, 3), "Negative", "", None,
                                "", "", "Discarded", ""])
    _sheet(jan, DAILY_HEADER, by_sheet["January"], title="Measles daily sheet")
    _sheet(mar, DAILY_HEADER, by_sheet["March"])
    paths["daily"] = folder / "Measles daily sheet.xlsx"
    wb.save(paths["daily"])

    wb = Workbook()
    dictionary = wb.active
    dictionary.title = "Data dictionary"
    dictionary.append(["Variable Name", "Description (EN)", "Description(FR)", "Type", "Format"])
    for name in LINE_HEADER:
        dictionary.append([name, "", "", "Text", ""])
    data = wb.create_sheet("Line list")
    rows = []
    for i, (acc, p) in enumerate(PATIENTS.items(), 1):
        if p[3] is None:
            continue
        rows.append(["United Arab Emirates", p[3], f"{p[3]} District", "", f"CASE-{i:03d}", 5, 60, "Male", "Test",
                     "0 dose", None, p[7], None, p[8], "Yes", "Yes", "Yes", p[4], p[4], p[4], p[5],
                     p[1].lower().replace("-", " "),  # LabID typed differently: "l 5001"
                     "Throat swab", "Adequate", p[6], "Positive", "Positive", "", "Imported case" if p[7] == "Yes"
                     else "Endemic", "Laboratory confirmed measles", "Recovered", ""])
    _sheet(data, LINE_HEADER, rows)
    paths["linelist"] = folder / "MOHAP line list.xlsx"
    wb.save(paths["linelist"])

    wb = Workbook()
    ws = wb.active
    ws.title = "MeaNS"
    from sanger_seq_analyzer.fetch.linking import MEANS_COLUMNS
    ws.append(list(MEANS_COLUMNS))
    for name in TEMPLATE_NAMES:
        ws.append([name] + [None] * (len(MEANS_COLUMNS) - 1))
    ws.cell(row=2, column=MEANS_COLUMNS.index("Case Onset Date") + 1).value = datetime(2026, 1, 1)
    ws.cell(row=2, column=MEANS_COLUMNS.index("Case Onset Date") + 1).number_format = "dd-mmm-yy"
    ws.cell(row=2, column=MEANS_COLUMNS.index("Accession") + 1).value = "02SV260000001"
    paths["template"] = folder / "MeaNS Upload Template.xlsx"
    wb.save(paths["template"])

    paths["genotyping"] = folder / "genotyping.tsv"
    paths["genotyping"].write_text(GENOTYPING_TSV, encoding="utf-8")
    return paths
