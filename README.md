# sanger_seq_analyzer

Sanger sequencing analysis for Windows and macOS (also Linux). It reads `.ab1`
chromatograms, checks the quality of each forward and reverse read, calls mixed
bases, and writes a forward/reverse consensus FASTA, in the spirit of
[RECall](https://recall.bccfe.ca/). QC is the first module. The package is set up so
more tools can be added as extra commands.

## What it does

For every sample with a forward (`_F`) and reverse (`_R`) read:

1. **Read QC.** A read **passes** when **at least 75% of its bases have Phred quality ≥ 20**
   (QV20 means 99% base-call accuracy, the same "QV20+" figure ABI software reports) and
   at least 100 bp remain after trimming. You can change both numbers.
2. **Trimming.** Low-quality ends are removed with Mott's algorithm (error limit 0.05,
   the method used by phred and Geneious).
3. **Mixture calling.** If a second peak is at least 20% as tall as the called peak, the
   base becomes an IUPAC code (R, Y, K, M, S, W, …). This is only done inside the trimmed region.
4. **Consensus.** The reverse read is reverse-complemented and aligned to the forward read.
   - Where both reads cover a position: a mixture is kept only if both reads support it.
     If the reads disagree, the base comes from the read that is clearly better (≥10 QV higher).
     Otherwise the base is `N`.
   - Where only one read covers a position: the base is kept if it is ≥ Q20, otherwise it is `N`.
   - The sample fails if the reads overlap by less than 20 bp or agree at less than 90% of the overlap.
5. **Reference (optional).** The consensus is turned to match the strand of a reference
   FASTA, and its identity to the reference is reported.

| Sample status | Meaning | Written to |
|---|---|---|
| `PASS` | both reads passed and they agree | `consensus.fasta` |
| `SINGLE` | both reads were provided but only one passed QC | `single_read.fasta` |
| `FAIL` | a forward or reverse file is missing, neither read passed, or the two reads do not match | no sequence. The reason is in the report |

### Outputs (in the output folder)

| File | Contents |
|---|---|
| `consensus.fasta` | consensus sequences of the PASS samples |
| `single_read.fasta` | single-read sequences of the SINGLE samples (marked as such) |
| `trimmed_reads.fasta` | every read that passed, after trimming |
| `sample_summary.csv` | one row per sample: status, length, overlap, mixtures, Ns, reasons |
| `read_qc.csv` | one row per read: % bases ≥ Q20, mean QV, trim positions, mixtures |
| `report.html` | report to open in a browser, with a quality plot for each read |

## Examples: good and bad

`examples/` contains simulated `.ab1` files built from `examples/reference.fasta`, so the
correct answer for each one is known. To rebuild them, run `python scripts/make_examples.py`.

| Sample | Folder | Forward | Reverse | Result | Why |
|---|---|---|---|---|---|
| GOOD01 | good | 96% ≥Q20 | 96% | **PASS** | clean pair. One SNP and one A/G mixture (`R`), and both are called |
| GOOD02 | good | 95% | 96% | **PASS** | clean pair. One C/T mixture (`Y`) |
| BORDER01 | good | 79% | 81% | **PASS** | just above the 75% cutoff |
| BAD01 | bad | 19% | 19% | **FAIL** | failed reaction: weak, noisy signal |
| BAD02 | bad | 96% | 70% | **SINGLE** | reverse read is just under 75%, so only the forward read is used |
| BAD03 | bad | 96% | 96% | **FAIL** | both reads are clean but come from different templates (sample mix-up) |

```
sanger-seq qc examples --reference examples/reference.fasta -o sanger_results
```

## Installing and running

### Option A: ready-made app (no Python needed)

The builds are made by GitHub Actions. Download `SangerSeqAnalyzer-windows.zip` or
`SangerSeqAnalyzer-macos.zip` from the **Actions** tab (latest run, then *Artifacts*) or
from **Releases**. A tag such as `v0.1.0` publishes a release.

- **Windows:** unzip, then run `SangerSeqAnalyzer\SangerSeqAnalyzer.exe` to open the window, or
  `sanger-seq.exe qc <folder>` from a command prompt.
- **macOS:** unzip and open `SangerSeqAnalyzer.app`. The app is not signed, so the first time
  you must right-click it and choose **Open**, or run `xattr -cr SangerSeqAnalyzer.app`.
  The build targets Apple Silicon. On Intel Macs, use option B.

### Option B: with Python (3.9 or newer, from python.org)

```
pip install git+https://github.com/OSAGSoliman/sanger_seq_analyzer.git
sanger-seq-gui                      # opens the window
sanger-seq qc path/to/ab1_folder -o results
```

### Command-line options

```
sanger-seq qc INPUTS... [-o OUTDIR]
  --cutoff 75              % of bases that must be good for a read to pass
  --min-quality 20         Phred value that counts as a good base
  --min-length 100         minimum trimmed length
  --mixture-threshold 20   secondary peak, as % of the main peak, that calls a mixture
  --no-mixtures            do not call mixtures
  --reference ref.fasta    turn the consensus to the reference strand and report identity
  --forward-tags F,FWD,FOR,FORWARD   --reverse-tags R,REV,REVERSE
  --no-html
```

**File naming:** the sample name is everything before the last direction tag. For example,
`HIV01_RT_F_A03.ab1` and `HIV01_RT_R_A04.ab1` both belong to the sample `HIV01_RT`. Tags can
be separated by `_`, `-`, `.` or a space, and upper/lower case does not matter. Files without
a tag are listed as unpaired.

## Development

```
pip install -e ".[dev]"
pytest
```

Code layout (`src/sanger_seq_analyzer/`):

| Module | Role |
|---|---|
| `abi.py` | reads `.ab1` files (base calls, qualities, peak positions, traces) |
| `qc.py` | trimming, mixture calling, PASS/FAIL per read |
| `consensus.py` | forward/reverse alignment, consensus, orientation to a reference |
| `pipeline.py` | finds files, pairs them, runs a batch |
| `report.py` | FASTA, CSV and HTML outputs |
| `cli.py` | `sanger-seq` commands. Add new tools here as subcommands |
| `gui.py` | desktop window (Tkinter, included with Python on Windows and macOS) |
| `simulate.py` | simulated chromatograms and an ABIF writer, used for the examples and tests |

CI runs the tests on Windows, macOS and Linux, then builds the Windows and macOS apps with PyInstaller.
