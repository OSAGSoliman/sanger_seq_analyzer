"""Fetch data: link genotyping results to the lab's spreadsheets and prepare MeaNS uploads.

* :mod:`.tables` reads Excel/CSV files and pasted tables, finding the header row
  even when it is not the first row, and combines sheets;
* :mod:`.linking` follows Accession -> Masterlist -> MRN -> Daily sheet and
  LabID -> MOHAP line list, and builds the MeaNS columns;
* :mod:`.strains` parses WHO strain names and gives new samples the next number;
* :mod:`.template` appends confirmed rows to the MeaNS upload template.

Nothing here imports Qt.
"""
