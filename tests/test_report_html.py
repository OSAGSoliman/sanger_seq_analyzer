"""The redesigned report.html (design spec section 4; FR HR-1, HR-2)."""

import html
import re
import shutil
import sys
from html.parser import HTMLParser

import pytest
from builders import EXAMPLES, REF

from sanger_seq_analyzer.pipeline import run
from sanger_seq_analyzer.report import write_outputs
from sanger_seq_analyzer.report_html import render_html


@pytest.fixture(scope="module")
def page(examples_run):
    return render_html(examples_run)


def _section(page, name):
    start = page.index(f'id="s-{name}"')
    end = page.find('<section class="card sample"', start)
    return page[start:end if end > 0 else len(page)]


class _Strict(HTMLParser):
    VOID = {"meta", "br", "input", "img", "hr", "link"}

    def __init__(self):
        super().__init__()
        self.stack, self.errors = [], []

    def handle_starttag(self, tag, attrs):
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1] != tag:
            self.errors.append((tag, self.stack[-3:]))
        else:
            self.stack.pop()


def test_structure(page):
    assert page.count('<section class="card sample"') == 11
    assert page.count("<canvas") == 21  # BAD07 has no reverse read
    assert "http://" not in page and "https://" not in page
    parser = _Strict()
    parser.feed(page)
    parser.close()
    assert parser.errors == [] and parser.stack == []


def test_sample_content(page):
    bad04 = _section(page, "BAD04")
    assert ("Reference positions 178–180 read TAG in the consensus (reference AAG). "
            "Both reads call T at position 178: forward Q55, reverse Q56.") in bad04
    assert 'class="tile bad"' in bad04 and "Not written to consensus.fasta" in bad04
    good01 = _section(page, "GOOD01")
    assert len(re.findall(r'<span class="d[ "]', good01)) == 1  # the C>A difference at reference 101
    assert len(re.findall(r'<span class="m[ "]', good01)) == 1  # the Y mixture at 281
    assert "Included in consensus.fasta" in good01 and "Flagged positions:" in good01
    assert "Ref 281 · codon 94, base 2" in good01
    bad07 = _section(page, "BAD07")
    assert "No reverse file for this sample" in bad07
    assert "<details><summary>Alignment of the forward read only" in bad07


def test_size_and_links(examples_run, tmp_path):
    path = write_outputs(examples_run, tmp_path, files=["html"])["html"]
    assert path.stat().st_size <= 2_000_000
    page = path.read_text(encoding="utf-8")
    for target in re.findall(r'href="#([^"]+)"', page):
        assert f'id="{target}"' in page, target


def test_names_are_escaped(tmp_path):
    # Windows does not allow < and > in file names; & and ' still need escaping there.
    name = "A&'x" if sys.platform == "win32" else "A<b>&'x"
    shutil.copy(EXAMPLES / "good/GOOD01_F.ab1", tmp_path / f"{name}_F.ab1")
    shutil.copy(EXAMPLES / "good/GOOD01_R.ab1", tmp_path / f"{name}_R.ab1")
    page = render_html(run([tmp_path], REF))
    assert html.escape(name) in page
    assert f">{name}<" not in page and f'"{name}"' not in page
    if "<" in name:
        assert "A<b>" not in page
