"""Reference sequences: built-ins selectable by name, FASTA files, validation and normalisation.

Each reference is the region reads are assembled against; its first base is the
first base of codon 1 unless a different frame is set.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from Bio import SeqIO

DNA_LETTERS = set("ACGTRYSWKMBDHVN")
PROTEIN_ONLY = set("EFIJLOPQZX*")
LONG_REFERENCE = 20_000  # alignment time grows linearly with the reference length


@dataclass(frozen=True)
class Reference:
    name: str
    description: str
    source: str
    sequence: str


REFERENCES = {
    "measles-n450": Reference(
        name="measles-n450",
        description="Measles virus N-450 (WHO genotyping region): last 450 nt of the nucleoprotein "
                    "gene, N codons 376-525",
        source="RefSeq NC_001498.1 (strain Ichinose-B95a), genome positions 1233-1682; "
               "N CDS 108..1685",
        sequence=(
        "GTCAGTTCCACATTGGCATCCGAACTCGGTATCACTGCCGAGGATGCAAGGCTTGTTTCA"
        "GAGATTGCAATGCATACTACTGAGGACAGGATCAGTAGAGCGGTCGGACCCAGACAAGCC"
        "CAAGTGTCATTTCTACACGGTGATCAAAGTGAGAATGAGCTACCAGGATTGGGGGGCAAG"
        "GAAGATAGGAGGGTCAAACAGGGTCGGGGAGAAGCCAGGGAGAGCTACAGAGAAACCGGG"
        "TCCAGCAGAGCAAGTGATGCGAGAGCTGCCCATCCTCCAACCAGCATGCCCCTAGACATT"
        "GACACTGCATCGGAGTCAGGCCAAGATCCGCAGGACAGTCGAAGGTCAGCTGACGCCCTG"
        "CTCAGGCTGCAAGCCATGGCAGGAATCTTGGAAGAACAAGGCTCAGACACGGACACCCCT"
        "AGGGTATACAATGACAGAGATCTTCTAGAC"
        ),
    ),
}


class ReferenceError(ValueError):
    """The reference cannot be used; the message says why and what to do."""


@dataclass
class ReferenceInfo:
    """A validated, normalised reference and where it came from (for traceability)."""

    name: str
    description: str
    source: str
    sequence: str
    path: Path | None = None
    record_id: str | None = None
    sha256: str = ""
    warnings: list[str] = field(default_factory=list)
    builtin: bool = False

    def __post_init__(self) -> None:
        if not self.sha256:
            self.sha256 = hashlib.sha256(self.sequence.encode("ascii")).hexdigest()

    @property
    def length(self) -> int:
        return len(self.sequence)


def get(name: str) -> Reference | None:
    return REFERENCES.get(name.strip().lower())


def normalize_sequence(text: str) -> tuple[str, list[str], list[str]]:
    """Clean a pasted or FASTA sequence; return (sequence, warnings, errors).

    Whitespace and digits are removed silently; ``U`` becomes ``T`` and gaps
    (``-`` or ``.``) are removed with a warning. Anything else that is not an IUPAC
    DNA letter is an error, as are an empty sequence, one without A, C, G or T,
    and one that looks like protein.
    """
    warnings: list[str] = []
    errors: list[str] = []
    seq = re.sub(r"[\s\d]+", "", text).upper()
    if "U" in seq:
        warnings.append("RNA letters converted: U read as T")
        seq = seq.replace("U", "T")
    gaps = sum(seq.count(c) for c in "-.")
    if gaps:
        warnings.append(f"{gaps} gap character(s) removed")
        seq = seq.replace("-", "").replace(".", "")
    if not seq:
        return "", warnings, ["no sequence found"]
    bad = sorted(set(seq) - DNA_LETTERS)
    acgt = sum(seq.count(c) for c in "ACGT")
    if set(seq) & PROTEIN_ONLY or acgt + seq.count("N") < 0.75 * len(seq):
        errors.append("this looks like a protein sequence; use the nucleotide sequence of the target")
    elif bad:
        errors.append("characters that are not IUPAC DNA letters: " + " ".join(bad))
    elif not acgt:
        errors.append("the sequence contains no A, C, G or T")
    return seq, warnings, errors


def fasta_records(path: str | Path) -> list[tuple[str, str, str]]:
    """(id, description, raw sequence text) of every record in a FASTA file."""
    with open(path, encoding="utf-8-sig", errors="replace") as fh:
        return [(r.id, r.description, str(r.seq)) for r in SeqIO.parse(fh, "fasta")]


def coding_warnings(sequence: str, frame: int = 1) -> list[str]:
    """Problems that would make every sample fail the coding checks."""
    out: list[str] = []
    stops = [n for n, i in enumerate(range(frame - 1, len(sequence) - 2, 3), 1)
             if sequence[i:i + 3] in {"TAA", "TAG", "TGA"}]
    if stops:
        out.append(f"the reference has a stop codon at codon {stops[0]} in frame {frame}; every sample "
                   "would fail the stop-codon rule (use Non-coding target, or check the frame)")
    if (len(sequence) - frame + 1) % 3:
        out.append(f"the reference length from position {frame} is not a multiple of 3 "
                   f"({len(sequence) - frame + 1} bp)")
    return out


def _finish(info: ReferenceInfo) -> ReferenceInfo:
    if info.length > LONG_REFERENCE:
        info.warnings.append(f"long reference ({info.length:,} bp): each read takes noticeably longer to align")
    return info


def from_sequence(text: str, name: str = "custom", description: str = "", source: str = "sequence") -> ReferenceInfo:
    seq, warnings, errors = normalize_sequence(text)
    if errors:
        raise ReferenceError(f"reference {name}: " + "; ".join(errors))
    return _finish(ReferenceInfo(name, description, source, seq, warnings=warnings))


def resolve_reference(spec: str | Path, record_id: str | None = None) -> ReferenceInfo:
    """A built-in reference by name (e.g. ``measles-n450``) or one record of a FASTA file."""
    builtin = get(str(spec))
    path = Path(spec)
    if builtin is not None and not path.is_file():
        return ReferenceInfo(builtin.name, builtin.description, builtin.source, builtin.sequence, builtin=True)
    if not path.is_file():
        raise ReferenceError(f"reference not found: {spec} (give a built-in name such as measles-n450 "
                             "or an existing FASTA file)")
    try:
        records = fasta_records(path)
    except (OSError, ValueError) as exc:
        raise ReferenceError(f"could not read reference {path.name}: {exc}") from exc
    if not records:
        raise ReferenceError(f"no FASTA record found in {path.name} (the file must start with a '>' header line)")
    if record_id is None:
        if len(records) > 1:
            ids = ", ".join(r[0] for r in records)
            raise ReferenceError(f"{path.name} holds {len(records)} records ({ids}); choose one")
        rid, desc, text = records[0]
    else:
        match = [r for r in records if r[0] == record_id]
        if not match:
            raise ReferenceError(f"record {record_id} not found in {path.name}")
        rid, desc, text = match[0]
    seq, warnings, errors = normalize_sequence(text)
    if errors:
        raise ReferenceError(f"{path.name}: " + "; ".join(errors))
    description = desc[len(rid):].strip() if desc.startswith(rid) else desc
    return _finish(ReferenceInfo(path.stem, description, str(path), seq, path=path,
                                 record_id=rid if len(records) > 1 or record_id else None, warnings=warnings))
