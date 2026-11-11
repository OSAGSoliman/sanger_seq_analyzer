# sanger_seq_analyzer

Sanger sequencing analysis for Windows and macOS (also Linux). It reads `.ab1`
chromatograms, applies your lab's read-quality cutoff, assembles forward and reverse
reads against a reference, and calls, marks and passes or fails each sample using the rules
of [RECall](https://recall.bccfe.ca/) (Woods et al., *J Clin Microbiol* 2012;50:1936-42).
Results are shown in a desktop window (verdict and reasons, rule checklist, metrics, alignment
and linked forward/reverse chromatograms) and written as FASTA, CSV, JSON and an HTML report.
QC is the first tool; the window and the command line are both set up so more tools can be
added next to it.

## The desktop window

Start it with `sanger-seq-gui` (or the `SangerSeqAnalyzer` app). It has three pages:

- **Run.** Choose the folder with `.ab1` files (or drop a folder or files onto the window; *Add
  files…* adds single files). A summary shows how the files were paired, which samples have only
  one direction, and which files have no direction tag; *Show pairing* lists every sample with
  its forward and reverse file. Choose the reference (`measles-n450` is preselected, *Custom
  FASTA…* adds your own) and the output folder, then **Run analysis**. The analysis runs in the
  background with a progress bar and can be cancelled; nothing is written for a cancelled run.
  *Load example data* appears when the bundled examples are available.
- **Results.** The summary strip shows the PASS/FAIL counts, the reference and whether the rules
  were changed. The sample list can be searched (by name, reason or failure type), filtered by
  status and sorted. For the selected sample you see the verdict with every reason in plain
  language (with a button that jumps to the position), eight metric tiles, and a panel with
  four views: **Alignment** (reference, consensus and both reads, with differences, mixtures,
  Ns, gaps, marks and stop codons highlighted), **Rules** (every rule with the observed value,
  the limit and pass/fail, also for samples that passed), **Flagged** (every flagged or marked
  position with each read's call, quality and secondary peak) and **Sequence** (FASTA, with copy
  buttons). Below are the **chromatograms** of the forward and reverse read with their read QC.
  Clicking a position in the alignment centres both traces on it, and clicking a peak selects the
  position in the alignment. The reverse read is shown reverse-complemented, so both read in
  reference orientation (right-click > *Show as sequenced* turns this off). Keys: Up/Down
  change sample, Left/Right move one position (Shift = 10, Ctrl = one codon), `[` and `]` jump to
  the previous/next flagged position, Ctrl/Cmd + wheel zooms the traces.
- **Settings.** Every rule, with its default; changed values are marked and can be reset one by
  one or all together. Settings apply to the next run; results computed with other settings are
  labelled as such until you run again.

**Export…** (Results page) writes any of the output files to a folder of your choice. By default
the files are also written to `<input folder>/sanger_results` when a run finishes. Settings,
folders and the window layout are remembered between sessions (*Help > Restore factory
settings* clears them).

## Rules

### 1. Lab read rules (checked first)

| Rule | Default |
|---|---|
| A read passes when this share of its bases are ≥ Q20 | **75%** |
| Minimum read length after trimming | 100 bp |
| Each sample needs both a forward and a reverse read | yes (`--allow-one-direction` turns this off) |
| An unreadable file fails its sample | always |
| The forward read must align to the plus strand and the reverse read to the minus strand; two reads on the same strand (for example the same file twice) always fail | yes (`--no-orientation-check` turns off the first part) |

If a sample fails any of these, it fails, and the report gives only that reason (the assembly is
still shown, labelled as display only).

### 2. RECall assembly, mixture calling and marks (Woods et al. 2012, Table 1)

- **Trimming and alignment.** Poor-quality read ends are trimmed with Mott's algorithm.
  Each read is aligned end to end against the reference (both strands tried; the read may
  extend beyond either end of the reference). Where a read ends inside the reference, the
  alignment is trimmed back to the first and last well-matching stretch (8 of 10 bases
  identical), so primer or noise cannot be aligned into the reference. A variant in the first or
  last base of the reference is kept.
- **Censoring.** Bases below **Q10** are left out of the assembly.
- **Peak areas.** For every base, the area of the called peak and of the largest
  secondary ("uncalled") peak are measured from the trace.
- **Calling each position.** At each reference position:
  1. Count each nucleotide that is either a called base, or an uncalled base whose area is
     **≥ 20%** of the called peak's area. A two-base IUPAC call written by the sequencer
     (R, Y, S, W, K, M) counts for both of its bases.
  2. Keep only nucleotides seen in at least half of the reads.
  3. If the second most common nucleotide has more than half the count of the most common
     one, call a **mixture** (IUPAC code). A tie between two bases is therefore also a mixture.
  4. Otherwise, call the most common base.
  5. Never call a three-base mixture.
- **Insertions and deletions.** A position is a deletion only when more reads show a gap than a
  base (a censored base still counts as a base). An inserted base is kept only when more reads
  carry it than continue without it; otherwise the position is marked.
- **Marks.** A position is **marked** (flagged for review) when:
  - the uncalled peak is ≥ **17.5%** of the called peak in at least half of the reads, or
  - the average quality across reads is below **Q20**, or
  - there is an insertion or a deletion (in all or some reads), or
  - only one read covers it.

### 3. RECall rejection criteria (Woods et al. 2012, Table 2)

A sample **fails** if any of these is true:

| Failure | Rule |
|---|---|
| Incomplete coverage | a reference position is not covered by both reads (by one read when one direction is allowed). RECall by default requires double coverage over the entire sequence; `--allow-partial-coverage` turns this off |
| Stop codon | any unambiguous TGA, TAA or TAG, read in the reference frame |
| Bad insert / bad deletion | an insertion or deletion relative to the reference that is not a multiple of 3 (frameshift) |
| Too many mixtures | more than 3.5% of bases are mixtures |
| N count | 5 or more Ns |
| Mark count | 100 or more marked positions |
| Single coverage | more than 3 consecutive bases covered by only one read and below Q40 |
| Low quality | any position where no read gives a usable call |

Every number can be changed, in the Settings page or on the command line (`sanger-seq qc
--help`). For a target that does not code for a protein, `--non-coding` (the *Non-coding target*
switch) turns off the stop-codon and frameshift checks. Alignment options: `--trim-error-limit`,
`--min-identity` (default 80%), `--min-aligned` (default 50 bp).

**The reference** is the region you want to sequence. Its first base is the first base of
codon 1; use `--frame` if codon 1 starts elsewhere. The consensus covers only that region.
You can give your own FASTA file, or the name of a built-in reference (run
`sanger-seq references` to list them):

| Name | Region | Source |
|---|---|---|
| `measles-n450` | Measles virus **N-450**, the WHO genotyping region: the last 450 nt of the nucleoprotein (N) gene, N codons 376-525 | RefSeq [NC_001498.1](https://www.ncbi.nlm.nih.gov/nuccore/NC_001498.1) (strain Ichinose-B95a), genome positions 1233-1682. Also saved as `references/measles_N450_NC_001498.fasta` |

```
sanger-seq qc my_ab1_folder -r measles-n450 -o results
```

A FASTA file with several records needs `--reference-record ID` (the window asks which one).
Whitespace and digits are ignored; `U` is read as `T` and gap characters are removed, with a
warning. Anything else that is not a DNA letter, an empty sequence or a protein sequence is an
error. You are warned when the reference has a stop codon in the chosen frame or its length is
not a multiple of 3.

Report positions are numbered within N-450 (1-450). To convert to genome positions, add 1232;
to convert to N codons, add 375 to the codon number.

**Differences from RECall.**
- RECall reruns phred to call bases. This tool uses the base calls and quality values already
  stored in the `.ab1` file (ABI KB basecaller, phred scale) and measures peak areas from the
  processed traces in the file.
- Reads are paired by file name (forward and reverse tags); RECall groups any number of primers.
- The lab read rule (75% ≥ Q20) is applied before RECall's rules, and a sample needs a forward
  and a reverse read on the expected strands.
- Two-base IUPAC calls from the sequencer count as two observations; an insertion or deletion
  seen in only one of two reads is not called (the position is marked).
- RECall also excludes low-quality regions (phred < 20) inside a read; here only bases below the
  censor quality (Q10) are left out.
- Codons and stop codons are counted on the reference fragment (codon 1 = its first base unless
  the frame is set).

## Outputs (in the output folder)

| File | Contents |
|---|---|
| `consensus.fasta` | consensus of every PASS sample, oriented to the reference |
| `report.html` | report to open in a browser (no internet needed): summary cards, a sortable and filterable sample table, the rules used, and per sample the verdict and reasons, metrics, the alignment against the reference and a chromatogram of every read. Clicking a base in the alignment scrolls the chromatograms to it. Prints one sample per page |
| `sample_summary.csv` | one row per sample: status, length, reference span, mixtures, Ns, marks, stop codons, indels, reasons |
| `read_qc.csv` | one row per read: % bases ≥ Q20, mean QV, trim positions, strand, alignment to reference |
| `marks.csv` | every marked position and why it was marked |
| `trimmed_reads.fasta` | every read that passed, after trimming |
| `run_info.json` | traceability: app version, start and end time, reference name, source and SHA-256, input folders, tags, every setting, counts, files not analysed and errors |

CSV files are UTF-8 with a byte-order mark, so Excel shows non-ASCII sample names correctly.
Files are written to a temporary name and then renamed, so a failed export never leaves a file
that looks complete. Files that were not analysed (no direction tag, a second file for the same
direction, not an `.ab1` file, a path that does not exist) are listed with the reason; hidden
and system files (`._name.ab1`, `.DS_Store`, `Thumbs.db`) are skipped.

## Examples: good and bad

`examples/` contains simulated `.ab1` files built on the real measles **N-450** sequence
(the built-in `measles-n450` reference). The sequence sits inside a simulated 1000 bp amplicon
with random flanking sequence, which is read by a forward and a reverse primer. Run them with
`-r measles-n450`; this is already the default in the desktop window (*Load example data*). Each
sample was built to give one outcome, so the correct result is known. To rebuild them, run
`python scripts/make_examples.py`.

| Sample | Result | Why |
|---|---|---|
| GOOD01 | **PASS** | clean pair. One SNP (C→A at 101) and one heterozygous position, called as a mixture (`Y` at 281) |
| GOOD02 | **PASS** | one mixture (`R` at 401), and an in-frame 3 bp deletion at 201 that is marked but allowed |
| BORDER01 | **PASS** | reads at 79% and 81% ≥ Q20, just above the 75% cutoff |
| BAD01 | FAIL | failed reaction: about 20% of bases ≥ Q20 |
| BAD02 | FAIL | reverse read at 70%, below the 75% cutoff |
| BAD03 | FAIL | the reverse read comes from another template (sample mix-up): "reverse read does not match the reference (no alignment)" |
| BAD04 | FAIL | stop codon at codon 60 |
| BAD05 | FAIL | 1 bp deletion (frameshift) |
| BAD06 | FAIL | 28 mixtures (6.2%, above 3.5%) |
| BAD07 | FAIL | forward read only. The reverse read is missing |
| BAD08 | FAIL | both reads pass 75% (76% and 77%), but their weak bases are spread through the target, so many positions are marked: 113 marked positions (fail at ≥ 100) |

```
sanger-seq qc examples -r measles-n450 -o sanger_results
```

## Installing and running

### Option A: ready-made app (no Python needed)

Download `SangerSeqAnalyzer-windows.zip` or `SangerSeqAnalyzer-macos.zip` from
[Releases](https://github.com/OSAGSoliman/sanger_seq_analyzer/releases). Each zip holds the
desktop app, the separate command-line tool `sanger-seq`, the examples and this README.

- **Windows:** unzip, then run `SangerSeqAnalyzer\SangerSeqAnalyzer.exe` to open the window, or
  `sanger-seq.exe qc <folder> -r measles-n450` from a command prompt. If SmartScreen warns
  you, choose *More info*, then *Run anyway*.
- **macOS:** unzip and open `SangerSeqAnalyzer.app`. The app is not signed, so the first time
  you must right-click it and choose **Open**, or run `xattr -cr SangerSeqAnalyzer.app`.
  The build targets Apple Silicon and needs the macOS version required by Qt 6.12. On Intel
  Macs, use option B.

### Option B: with Python (3.10 or newer, from python.org)

```
pip install git+https://github.com/OSAGSoliman/sanger_seq_analyzer.git
sanger-seq-gui                                        # opens the window
sanger-seq qc path/to/ab1_folder -r measles-n450 -o results   # or -r your_reference.fasta
```

This installs PySide6-Essentials (Qt) for the window. The command line never loads Qt, so
`sanger-seq` also works where Qt cannot be installed (`pip install --no-deps` plus `biopython`
and `numpy`); `sanger-seq gui` then explains how to add the window.

**File naming:** the sample name is everything before the last direction tag (F, FWD, FOR,
FORWARD / R, REV, REVERSE; change them in Settings or with `--forward-tags`/`--reverse-tags`).
For example, `HIV01_RT_F_A03.ab1` and `HIV01_RT_R_A04.ab1` both belong to the sample `HIV01_RT`.
Tags can be separated by `_`, `-`, `.` or a space and are matched as literal text.

## Development

```
pip install -e ".[dev]"
pytest                                   # backend and window tests (the window tests run headless)
python scripts/screenshots.py shots --theme both   # every screen of the window as PNG
sanger-seq-gui --self-test st            # headless end-to-end check (st/self_test.json)
python packaging/build.py                # PyInstaller builds in dist/ (needs ".[build]")
```

| Module | Role |
|---|---|
| `abi.py` | reads `.ab1` files (base calls, qualities, peak positions, traces) |
| `qc.py` | settings, trimming, peak areas, the lab read cutoff |
| `recall.py` | alignment to the reference, RECall voting, marks and rejection rules, the rule checklist |
| `pipeline.py` | finds and pairs files (with a reason for every file left out), reads them once, runs a batch with progress and cancel |
| `references.py` | built-in references, FASTA reading, validation and normalisation |
| `settings_schema.py` | label, unit, range and help text of every setting; validation and storage |
| `presentation.py` | the wording and view data shared by the window and the report |
| `design_tokens.py` | colours and type scale shared by the window and the report |
| `report.py`, `report_html.py`, `report_assets.py` | output files; the HTML report and its CSS/JavaScript |
| `cli.py` | `sanger-seq` commands. Add new tools here as subcommands |
| `gui/` | desktop window (PySide6). `gui/tools.py` registers tools; `gui/qc/` is the QC tool |
| `simulate.py` | simulated chromatograms and an ABIF writer, used for the examples and tests |

CI runs the tests on Windows, macOS and Linux (Python 3.10 and 3.12), checks that the command
line works without Qt, then builds the Windows and macOS apps with PyInstaller and runs the
packaged command line and the app's `--self-test` before uploading them. Pushing a `v*` tag
publishes a release.

### Not yet

Planned but not in this version: recent-folder list, manual pairing override, folder watching,
filename tag presets (for example primer names), display offsets for genome or codon numbering,
a reference library, named settings presets, re-running a single sample, run notifications,
a failure breakdown by rule, plate charts, column choice in the sample list, exporting only
selected samples, reviewer notes, horizontal alignment mode, amino-acid row, go-to-position,
alignment overview strip, copying an alignment range, per-channel trace toggles, chromatogram
image export, instrument metadata, a quality plot per read, a "Reads" table, per-sample PDF,
saving a run as a project, static print windows of the traces in the report, a report without
traces, single-key navigation shortcuts (N, M, D, I), a shortcut list, translations, update
checks, a colour-blind palette, more than two reads per sample, `S5_F1`-style tags, an app icon,
code signing and notarisation, and an Intel macOS build. Validation with real `.ab1` files
from the lab's sequencer is still to be done before release.
