# sanger_seq_analyzer

Sanger sequencing analysis for Windows and macOS (also Linux). It reads `.ab1`
chromatograms, applies your lab's read-quality cutoff, assembles forward and reverse
reads against a reference, and calls, marks and passes or fails each sample using the rules
of [RECall](https://recall.bccfe.ca/) (Woods et al., *J Clin Microbiol* 2012;50:1936-42).
Samples that pass are exported as FASTA. QC is the first module. The package is set up so
more tools can be added as extra commands.

## Rules

### 1. Lab read rules (checked first)

| Rule | Default |
|---|---|
| A read passes when this share of its bases are ≥ Q20 | **75%** |
| Minimum read length after trimming | 100 bp |
| Each sample needs both a forward and a reverse read | yes (`--allow-one-direction` turns this off) |

If a sample fails any of these, it fails, and the report gives only that reason.

### 2. RECall assembly, mixture calling and marks (Woods et al. 2012, Table 1)

- **Trimming and alignment.** Poor-quality read ends are trimmed with Mott's algorithm.
  Each read is then aligned to the reference (local Smith-Waterman, both strands tried).
- **Censoring.** Bases below **Q10** are left out of the assembly.
- **Peak areas.** For every base, the area of the called peak and of the largest
  secondary ("uncalled") peak are measured from the trace.
- **Calling each position.** At each reference position:
  1. Count each nucleotide that is either a called base, or an uncalled base whose area is
     **≥ 20%** of the called peak's area.
  2. Keep only nucleotides seen in at least half of the reads.
  3. If the second most common nucleotide has more than half the count of the most common
     one, call a **mixture** (IUPAC code). A tie between two bases is therefore also a mixture.
  4. Otherwise, call the most common base.
  5. Never call a three-base mixture.
- **Marks.** A position is **marked** (flagged for review) when:
  - the uncalled peak is ≥ **17.5%** of the called peak in at least half of the reads, or
  - the average quality across reads is below **Q20**, or
  - there is an insertion or a deletion, or
  - only one read covers it.

### 3. RECall rejection criteria (Woods et al. 2012, Table 2)

A sample **fails** if any of these is true:

| Failure | Rule |
|---|---|
| Stop codon | any unambiguous TGA, TAA or TAG, read in the reference frame |
| Bad insert / bad deletion | an insertion or deletion relative to the reference that is not a multiple of 3 (frameshift) |
| Too many mixtures | more than 3.5% of bases are mixtures |
| N count | 5 or more Ns |
| Mark count | 100 or more marked positions |
| Single coverage | more than 3 consecutive bases covered by only one read and below Q40 |
| Low quality | any position where no read gives a usable call |

Every number can be changed. Run `sanger-seq qc --help` to see the options. For a target
that does not code for a protein, `--non-coding` turns off the stop-codon and frameshift checks.

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

In the desktop window, the reference box is set to `measles-n450` by default.

Report positions are numbered within N-450 (1-450). To convert to genome positions, add 1232;
to convert to N codons, add 375 to the codon number.

**Differences from RECall.** RECall reruns phred to call bases. This tool uses the base calls
and quality values already stored in the `.ab1` file, which come from the ABI KB basecaller and
are on the phred scale. It measures peak areas from the processed traces in the file.

## Outputs (in the output folder)

| File | Contents |
|---|---|
| `consensus.fasta` | consensus of every PASS sample, oriented to the reference |
| `report.html` | report to open in a browser: summary table, reasons, an alignment of each consensus against the reference (differences, mixtures, Ns and marked positions highlighted, with details on hover), and a scrollable chromatogram of every read (trace peaks, base calls, quality bars, trimmed ends shaded) |
| `sample_summary.csv` | one row per sample: status, length, reference span, mixtures, Ns, marks, stop codons, indels, reasons |
| `read_qc.csv` | one row per read: % bases ≥ Q20, mean QV, trim positions, strand, alignment to reference |
| `marks.csv` | every marked position and why it was marked |
| `trimmed_reads.fasta` | every read that passed, after trimming |

## Examples: good and bad

`examples/` contains simulated `.ab1` files. A synthetic 1000 bp amplicon is read by a
forward and a reverse primer. `examples/reference.fasta` is the 561 bp (187-codon)
protein-coding target inside it. Each sample was built to give one outcome, so the correct
result is known. To rebuild them, run `python scripts/make_examples.py`.

| Sample | Result | Why |
|---|---|---|
| GOOD01 | **PASS** | clean pair. One SNP and one heterozygous position, called as a mixture (`R`) |
| GOOD02 | **PASS** | one mixture (`Y`), and an in-frame 3 bp deletion that is marked but allowed |
| BORDER01 | **PASS** | reads at 79% and 81% ≥ Q20, just above the 75% cutoff |
| BAD01 | FAIL | failed reaction: about 20% of bases ≥ Q20 |
| BAD02 | FAIL | reverse read at 70%, below the 75% cutoff |
| BAD03 | FAIL | the reverse read comes from another template (sample mix-up) and does not match the reference |
| BAD04 | FAIL | stop codon at codon 60 |
| BAD05 | FAIL | 1 bp deletion (frameshift) |
| BAD06 | FAIL | 30 mixtures (5.3%, above 3.5%) |
| BAD07 | FAIL | forward read only. The reverse read is missing |
| BAD08 | FAIL | both reads pass 75% (79% and 81%), but their weak bases are spread through the target. RECall rejects it: too many marks and positions with no usable call |

```
sanger-seq qc examples -r examples/reference.fasta -o sanger_results
```

## Installing and running

### Option A: ready-made app (no Python needed)

Download `SangerSeqAnalyzer-windows.zip` or `SangerSeqAnalyzer-macos.zip` from
[Releases](https://github.com/OSAGSoliman/sanger_seq_analyzer/releases).

- **Windows:** unzip, then run `SangerSeqAnalyzer\SangerSeqAnalyzer.exe` to open the window, or
  `sanger-seq.exe qc <folder> -r reference.fasta` from a command prompt. If SmartScreen warns
  you, choose *More info*, then *Run anyway*.
- **macOS:** unzip and open `SangerSeqAnalyzer.app`. The app is not signed, so the first time
  you must right-click it and choose **Open**, or run `xattr -cr SangerSeqAnalyzer.app`.
  The build targets Apple Silicon. On Intel Macs, use option B.

### Option B: with Python (3.9 or newer, from python.org)

```
pip install git+https://github.com/OSAGSoliman/sanger_seq_analyzer.git
sanger-seq-gui                                        # opens the window
sanger-seq qc path/to/ab1_folder -r reference.fasta -o results
```

**File naming:** the sample name is everything before the last direction tag (F, FWD, FOR,
FORWARD / R, REV, REVERSE). For example, `HIV01_RT_F_A03.ab1` and `HIV01_RT_R_A04.ab1` both
belong to the sample `HIV01_RT`. Tags can be separated by `_`, `-`, `.` or a space. Files
without a tag are listed in the report as not analysed.

## Development

```
pip install -e ".[dev]"
pytest
```

| Module | Role |
|---|---|
| `abi.py` | reads `.ab1` files (base calls, qualities, peak positions, traces) |
| `qc.py` | settings, trimming, peak areas, the lab read cutoff |
| `recall.py` | alignment to the reference, RECall voting, marks and rejection rules |
| `pipeline.py` | finds files, pairs them, runs a batch |
| `report.py` | FASTA, CSV and HTML (chromatogram viewer and alignment view) |
| `cli.py` | `sanger-seq` commands. Add new tools here as subcommands |
| `gui.py` | desktop window (Tkinter, included with Python on Windows and macOS) |
| `simulate.py` | simulated chromatograms and an ABIF writer, used for the examples and tests |

CI runs the tests on Windows, macOS and Linux, then builds the Windows and macOS apps with
PyInstaller. Pushing a `v*` tag publishes a release.
