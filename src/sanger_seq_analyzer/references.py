"""Built-in reference sequences, selectable by name instead of a FASTA file.

Each entry is the region reads are assembled against; its first base is the
first base of codon 1.
"""

from __future__ import annotations

from dataclasses import dataclass


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


def get(name: str) -> Reference | None:
    return REFERENCES.get(name.strip().lower())
