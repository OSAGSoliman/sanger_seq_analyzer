"""Reviewer decisions and export scope.

After a run, a person can confirm each sample as PASS or FAIL. A confirmation that
differs from the rules overrides the status everywhere the result is exported
(FASTA, CSV, report); the summary CSV keeps the status the rules gave.

Exports can be limited to all samples, the passing ones, the failing ones, or a
chosen list. PASS/FAIL here means the status after review.
"""

from __future__ import annotations

import dataclasses

from .pipeline import RunResult, SampleResult
from .recall import Reason

STATUSES = ("PASS", "FAIL")
SCOPES = {
    "all": "All samples",
    "pass": "PASS only",
    "fail": "FAIL only",
    "selected": "Chosen samples",
}


def effective_status(sr: SampleResult, reviews: dict[str, str]) -> str:
    """The reviewer's decision when there is one, else the status the rules gave."""
    decision = reviews.get(sr.sample)
    return decision if decision in STATUSES else sr.consensus.status


def review_label(sr: SampleResult, reviews: dict[str, str]) -> str:
    """"" (not reviewed), "confirmed" (agrees with the rules) or "changed" (overrides them)."""
    decision = reviews.get(sr.sample)
    if decision not in STATUSES:
        return ""
    return "confirmed" if decision == sr.consensus.status else "changed"


def count(result: RunResult, reviews: dict[str, str], status: str) -> int:
    return sum(1 for s in result.samples if effective_status(s, reviews) == status)


def reviewed_count(result: RunResult, reviews: dict[str, str]) -> int:
    return sum(1 for s in result.samples if reviews.get(s.sample) in STATUSES)


def select(result: RunResult, reviews: dict[str, str], scope: str, chosen=()) -> list[str]:
    """Names of the samples an export with ``scope`` covers, in run order."""
    if scope not in SCOPES:
        raise ValueError(f"unknown export scope {scope!r}")
    wanted = set(chosen)
    out = []
    for s in result.samples:
        status = effective_status(s, reviews)
        if (scope == "all" or (scope == "pass" and status == "PASS") or (scope == "fail" and status == "FAIL")
                or (scope == "selected" and s.sample in wanted)):
            out.append(s.sample)
    return out


def _reviewed_sample(sr: SampleResult, decision: str) -> SampleResult:
    c = sr.consensus
    auto = c.status
    if decision == auto:
        return dataclasses.replace(sr, review=decision, auto_status=auto)
    text = f"reviewer changed the status from {auto} to {decision}"
    consensus = dataclasses.replace(c, status=decision, reason_items=[*c.reason_items, Reason("review", text)])
    return dataclasses.replace(sr, consensus=consensus, review=decision, auto_status=auto)


def apply(result: RunResult, reviews: dict[str, str] | None = None, names=None) -> RunResult:
    """A copy of ``result`` limited to ``names`` (all when None) with reviewer decisions applied.

    The original result is not changed.
    """
    reviews = reviews or {}
    keep = None if names is None else set(names)
    samples = []
    for sr in result.samples:
        if keep is not None and sr.sample not in keep:
            continue
        decision = reviews.get(sr.sample)
        samples.append(_reviewed_sample(sr, decision) if decision in STATUSES else sr)
    return dataclasses.replace(result, samples=samples)
