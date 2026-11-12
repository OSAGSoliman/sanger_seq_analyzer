"""Fetch data: reading the lab spreadsheets, linking, WHO strain names and the template write-back."""

from datetime import date, datetime

import pytest

pytest.importorskip("openpyxl")

from fetch_builders import GENOTYPING_TSV, build  # noqa: E402
from openpyxl import load_workbook  # noqa: E402

from sanger_seq_analyzer.fetch import linking, strains, tables, template  # noqa: E402
from sanger_seq_analyzer.fetch.linking import DAILY, GENOTYPING, LINELIST, MASTERLIST, TEMPLATE  # noqa: E402


@pytest.fixture
def files(tmp_path):
    return build(tmp_path)


def _read(spec, path):
    return tables.read_table(path, spec.title, list(spec.expected), spec.match_column, combine=spec.combine)


def _linked(files):
    geno = tables.text_table(GENOTYPING_TSV, GENOTYPING.title, list(GENOTYPING.expected), "Accession")
    return linking.link(geno, _read(MASTERLIST, files["masterlist"]), _read(DAILY, files["daily"]),
                        _read(LINELIST, files["linelist"]))


def test_keys_and_dates_are_normalised():
    assert tables.norm_key(100003.0) == tables.norm_key("0100003") == tables.norm_key(" 100003 ") == "100003"
    assert tables.norm_key("L-5001") == tables.norm_key("l 5001")
    assert tables.parse_date("09-03-2026") == date(2026, 3, 9)
    assert tables.parse_date("09/03/2026 10:15") == date(2026, 3, 9)
    assert tables.parse_date(46090) == date(2026, 3, 9)  # Excel serial
    assert tables.parse_date("pending") is None
    assert tables.match_column("Measles IgM result (First sample)",
                               ["Measles IgM result                (First sample)"])
    assert tables.match_column("MeaslesIgM (pending refers to not tested)", ["Sex", "MeaslesIgM"]) == "MeaslesIgM"
    assert tables.match_column("Lab ID #", ["Lab ID # "]) == "Lab ID # "


def test_pasted_genotyping_table_skips_the_second_header_line():
    t = tables.text_table(GENOTYPING_TSV, "Genotyping", list(GENOTYPING.expected), "Accession")
    assert [r["Accession"] for r in t.rows][:2] == ["02SV269990001", "02SV269990002"]
    assert len(t.rows) == 6 and t.column("Genotyping Result") == "Genotyping Result"


def test_headers_found_below_titles_and_sheets_combined(files):
    master = _read(MASTERLIST, files["masterlist"])
    assert len(master.rows) == 6 and master.column("ACCESSION") == "ACCESSION"
    daily = _read(DAILY, files["daily"])
    assert daily.sheets == ["January", "March"] and "Notes" in daily.skipped_sheets
    assert daily.column("Date of sample collection (2)")  # repeated header numbered
    line = _read(LINELIST, files["linelist"])
    assert line.sheets == ["Line list"] and len(line.rows) == 5  # the data dictionary sheet is ignored


def test_wrong_file_gives_a_readable_error(files):
    with pytest.raises(tables.TableError, match="LabID"):
        _read(LINELIST, files["masterlist"])
    with pytest.raises(tables.TableError, match="xlsx"):
        tables.read_sheets(files["masterlist"].with_suffix(".xls").touch() or files["masterlist"].with_suffix(".xls"))


def test_linking_follows_accession_mrn_and_labid(files):
    linked = _linked(files)
    by = {s.accession: s for s in linked.samples}
    s3 = by["02SV269990003"]
    assert all(s3.found.values())
    assert s3.rows["daily"]["Lab ID #"] == "L-5003"  # the row with the same lab id, not the older one
    assert s3.linelist_by == "masterlist LAB ID"
    s6 = by["02SV269990006"]
    assert s6.found == {"genotyping": True, "masterlist": True, "daily": False, "linelist": False}
    assert any("not found in the daily sheet" in n for n in s6.notes)
    merged = linked.merged(s3)
    assert merged["Line list · CASE_ID"] == "CASE-003" and merged["Masterlist · REGION"] == "DUBAI"


def test_means_rows_and_strain_names_continue_the_template(files):
    linked = _linked(files)
    existing = [r["WHO Strain Name"] for r in _read(TEMPLATE, files["template"]).rows]
    rows = {r.accession: r for r in linking.build_rows(linked, "MVs", existing, today=date(2026, 3, 25))}
    r3, r4, r5, r6 = (rows[f"02SV26999000{i}"] for i in (3, 4, 5, 6))
    # Dubai week 11 already has 1, 4 and 7 (written "11.26.7"): next are 8 and 9, in onset order
    assert r3.values["WHO Strain Name"] == "MVs/Dubai.ARE/11.26/8"
    assert r4.values["WHO Strain Name"] == "MVs/Dubai.ARE/11.26/9"
    # Al Ain -> Abu Dhabi; 1 Jan 2026 is ISO week 1 of 2026, where Abu Dhabi already has a name
    assert r5.values["WHO Strain Name"] == "MVs/Abu Dhabi.ARE/1.26/2"
    # no line list row: region and collection date from the masterlist
    assert r6.values["WHO Strain Name"] == "MVs/Ajman.ARE/12.26"
    assert r3.values["Case Onset Date"] == date(2026, 3, 11) and r3.values["Exporting Country"] == "India"
    assert r3.values["Case ID (MRN)"] == 100003 and r3.values["LINELIST ID"] == "CASE-003"
    assert r3.values["TAT"] == 10 and r3.values["to upload?"] == "Yes" and r3.include
    assert r3.sources["Case Onset Date"] == "Line list: DONSET"
    failed = rows["02SV269990001"]
    assert failed.values["to upload?"] == "No" and failed.values["WHO Strain Name"] == "" and not failed.include


def test_manual_strain_name_is_kept_and_reserved(files):
    linked = _linked(files)
    rows = linking.build_rows(linked, "MVs", [], today=date(2026, 3, 25))
    by = {r.accession: r for r in rows}
    by["02SV269990004"].values["WHO Strain Name"] = "MVs/Dubai.ARE/11.26"
    by["02SV269990004"].strain_manual = True
    linking.assign_strain_names(rows, "MVs", [])
    assert by["02SV269990003"].values["WHO Strain Name"] == "MVs/Dubai.ARE/11.26/2"


def test_strain_name_parsing():
    assert str(strains.parse("MVs/Dubai.ARE/11.26.7")) == "MVs/Dubai.ARE/11.26/7"
    assert strains.parse("MVs/Abu Dhabi.ARE/3.26").number == 1
    assert strains.parse("MVs/Dubai.ARE/19.26/6 ").number == 6
    assert strains.parse("not a name") is None
    assert strains.city_name("Ras Al Khaimah") == ("RAK", True)
    assert strains.city_name("DUBAI EMIRATE") == ("Dubai", True)
    assert strains.city_name("Hatta")[1] is False
    assert strains.week_year(date(2027, 1, 1)) == (53, 26)


def test_append_to_template_backs_up_and_continues_below_the_last_row(files):
    linked = _linked(files)
    existing = [r["WHO Strain Name"] for r in _read(TEMPLATE, files["template"]).rows]
    rows = [r for r in linking.build_rows(linked, "MVs", existing, today=date(2026, 3, 25)) if r.include]
    result = template.append_rows(files["template"], [r.values for r in rows], list(linking.MEANS_COLUMNS))
    assert result.written == 4 and result.backup is not None and result.backup.is_file()
    assert result.first_row == 9  # header + 7 rows (blank rows inside the table are kept)
    ws = load_workbook(files["template"]).active
    names = [ws.cell(row=r, column=1).value for r in range(9, 13)]
    assert "MVs/Dubai.ARE/11.26/8" in names
    onset = ws.cell(row=9, column=linking.MEANS_COLUMNS.index("Case Onset Date") + 1)
    assert isinstance(onset.value, datetime) and onset.number_format == "dd-mmm-yy"  # style of the row above
    # the next fetch sees the new names; saving the same rows again writes nothing
    again = template.append_rows(files["template"], [r.values for r in rows], list(linking.MEANS_COLUMNS))
    assert again.written == 0 and len(again.skipped) == 4
    new_existing = [r["WHO Strain Name"] for r in _read(TEMPLATE, files["template"]).rows]
    assert "MVs/Dubai.ARE/11.26/9" in new_existing


def test_template_open_in_excel_is_refused(files):
    (files["template"].parent / ("~$" + files["template"].name)).write_text("")
    with pytest.raises(tables.TableError, match="open in Excel"):
        template.append_rows(files["template"], [{"WHO Strain Name": "MVs/Dubai.ARE/1.26/9"}],
                             list(linking.MEANS_COLUMNS))


def test_genotyping_table_from_a_qc_run():
    from sanger_seq_analyzer.pipeline import run
    from builders import EXAMPLES
    result = run([EXAMPLES / "bad"], "measles-n450")
    t = linking.genotyping_from_run(result)
    comments = {r["Accession"]: r["COMMENTS"] for r in t.rows}
    assert comments["BAD02"] == "FAIL REINJECT REVERSE STRAND"
    assert comments["BAD07"].startswith("FAIL REINJECT REVERSE")  # missing reverse read
    assert comments["BAD01"] == "FAIL REINJECT BOTH STRANDS"


def test_workbook_export(tmp_path):
    p = template.write_workbook(tmp_path / "out.xlsx", [("MeaNS", ["A", "Date"], [["x", date(2026, 3, 1)]])])
    ws = load_workbook(p).active
    assert ws["A1"].value == "A" and ws["B2"].number_format == "dd/mm/yyyy"
