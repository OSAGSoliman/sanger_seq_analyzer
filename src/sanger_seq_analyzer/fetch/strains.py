"""WHO strain names: ``MVs/Dubai.ARE/30.26/2``.

Parts: virus (``MVs`` measles, ``RVs`` rubella), city (Admin Level 1) and
country code, ISO week and two-digit ISO year of the onset date, and a number
when the same city and week were already used (the first has no number, the
next is ``/2``, then ``/3`` ...). Existing names come from the MeaNS upload
template so new samples continue the numbering.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

COUNTRY = "ARE"
VIRUSES = {"MVs": "Measles", "RVs": "Rubella"}

# Spellings seen in the lab's files -> the city name used in strain names.
CITY_NAMES = {
    "dubai": "Dubai",
    "abu dhabi": "Abu Dhabi", "abudhabi": "Abu Dhabi",
    "al ain": "Abu Dhabi", "alain": "Abu Dhabi", "al dhafra": "Abu Dhabi", "aldhafra": "Abu Dhabi",
    "western region": "Abu Dhabi",
    "sharjah": "Sharjah", "shj": "Sharjah",
    "ajman": "Ajman",
    "rak": "RAK", "ras al khaimah": "RAK", "ras alkhaimah": "RAK", "ras al khaima": "RAK",
    "fujairah": "Fujairah", "fujeirah": "Fujairah", "fujaira": "Fujairah",
    "uaq": "UAQ", "umm al quwain": "UAQ", "umm al qaiwain": "UAQ", "umm alquwain": "UAQ",
}

NAME_RE = re.compile(
    r"^\s*(?P<virus>[MR]Vs)\s*/\s*(?P<city>[^/]+?)\s*\.\s*(?P<country>[A-Za-z]{3})\s*/\s*"
    r"(?P<week>\d{1,2})\s*\.\s*(?P<yy>\d{2})(?:\s*[/.]\s*(?P<n>\d+))?", re.IGNORECASE)


@dataclass(frozen=True)
class StrainName:
    virus: str
    city: str
    country: str
    week: int
    yy: int
    number: int  # 1 when the name has no number

    @property
    def key(self) -> tuple:
        return self.virus.upper(), self.city.casefold(), self.country.upper(), self.week, self.yy

    def __str__(self) -> str:
        base = f"{self.virus}/{self.city}.{self.country}/{self.week}.{self.yy:02d}"
        return base if self.number <= 1 else f"{base}/{self.number}"


def parse(text) -> StrainName | None:
    """A strain name from text; tolerates extra spaces and ``11.26.7`` for ``11.26/7``. None if not a name."""
    m = NAME_RE.match(str(text or ""))
    if not m:
        return None
    virus = "MVs" if m["virus"].upper().startswith("M") else "RVs"
    return StrainName(virus, m["city"].strip(), m["country"].upper(), int(m["week"]), int(m["yy"]),
                      int(m["n"]) if m["n"] else 1)


def city_name(admin1) -> tuple[str, bool]:
    """(city for the strain name, known). Unknown values are returned title-cased with known=False."""
    text = re.sub(r"[-_]", " ", str(admin1 or "")).casefold()
    text = re.sub(r"\b(emirate|city|region of)\b", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return "", False
    if text in CITY_NAMES:
        return CITY_NAMES[text], True
    squashed = text.replace(" ", "")
    for k, v in CITY_NAMES.items():
        if k.replace(" ", "") == squashed:
            return v, True
    return " ".join(w.capitalize() for w in text.split()), False


def week_year(day: date) -> tuple[int, int]:
    """ISO week and two-digit ISO year (1 Jan 2027 is week 53 of 2026 -> (53, 26))."""
    iso = day.isocalendar()
    return iso[1], iso[0] % 100


class Numbering:
    """Highest number used per (virus, city, country, week, year), from existing names and new ones."""

    def __init__(self, existing=()):
        self.used: dict[tuple, set[int]] = {}
        self.unparsed: list[str] = []
        for text in existing:
            if text is None or not str(text).strip():
                continue
            name = parse(text)
            if name is None:
                self.unparsed.append(str(text))
            else:
                self.add(name)

    def add(self, name: StrainName) -> None:
        self.used.setdefault(name.key, set()).add(name.number)

    def exists(self, name: StrainName) -> bool:
        return name.number in self.used.get(name.key, set())

    def next_name(self, virus: str, city: str, day: date, country: str = COUNTRY) -> StrainName:
        """The next free name for this city and week (after the highest number used), and reserve it."""
        week, yy = week_year(day)
        probe = StrainName(virus, city, country, week, yy, 1)
        used = self.used.get(probe.key, set())
        name = StrainName(virus, city, country, week, yy, max(used) + 1 if used else 1)
        self.add(name)
        return name
