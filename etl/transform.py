"""
etl/transform.py
----------------
Core ETL logic for the SF Homeless Pilot.

Responsibilities:
  1. Load demographics and anxiety CSVs (from local paths or S3 URIs)
  2. Normalize the HID key format so both datasets share a common key
  3. Join the datasets and emit a clean, merged list of records
  4. Validate the output and report any unmatched rows

HID format notes
----------------
Demographics CSV  :  "001-15"   (zero-padded sequence + year suffix)
Anxiety CSV       :  "HM15-1"   (prefix + year + un-padded sequence)

Canonical form used internally: demographics format  →  "001-15"

Transformation:
  HM{year}-{seq}  →  {seq:03d}-{year}
  e.g.  HM15-1  →  001-15
        HM15-18 →  018-15
"""

from __future__ import annotations

import csv
import io
import logging
import re
from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from typing import Iterator, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class DemographicsRecord:
    hid: str                        # canonical form: "001-15"
    registration_date: Optional[date]
    gender: str
    race: str
    shelter: str
    date_of_birth: Optional[date]

    # Names are present in the raw file but are NOT surfaced in the merged
    # output – they are PII-adjacent even though the data is de-identified.
    # We parse them only to validate row integrity.
    _raw_first: str = field(repr=False, default="")
    _raw_last:  str = field(repr=False, default="")


@dataclass
class EncounterRecord:
    hid: str                        # canonical form: "001-15"
    encounter_date: Optional[date]
    anxiety_level: Optional[int]    # 0-10 scale; None if unparseable


@dataclass
class MergedRecord:
    hid: str
    shelter: str
    gender: str
    race: str
    date_of_birth: Optional[date]
    registration_date: Optional[date]
    encounter_date: Optional[date]
    anxiety_level: Optional[int]

    def to_dict(self) -> dict:
        d = asdict(self)
        # Serialise dates to ISO strings for DynamoDB / JSON transport
        for k, v in d.items():
            if isinstance(v, date):
                d[k] = v.isoformat()
        return d


# ---------------------------------------------------------------------------
# HID normalisation
# ---------------------------------------------------------------------------

# Matches the anxiety-file format:  HM15-18, HM15-1, etc.
_ANXIETY_HID_RE = re.compile(r'^HM(\d+)-(\d+)$', re.IGNORECASE)

# Matches the demographics-file format: 001-15, 019-15, etc.
_DEMO_HID_RE = re.compile(r'^(\d{3})-(\d+)$')


def normalize_hid(raw: str) -> str:
    """
    Convert any supported HID variant to the canonical demographics format.

    Supported inputs
    ----------------
    "HM15-1"   →  "001-15"
    "HM15-18"  →  "018-15"
    "001-15"   →  "001-15"   (already canonical – returned unchanged)

    Raises ValueError for unrecognised formats so callers can log & skip.
    """
    raw = raw.strip()

    # Already canonical?
    if _DEMO_HID_RE.match(raw):
        return raw

    # Anxiety format: HM{year}-{seq}
    m = _ANXIETY_HID_RE.match(raw)
    if m:
        year, seq = m.group(1), m.group(2)
        return f"{int(seq):03d}-{year}"

    raise ValueError(f"Unrecognised HID format: {raw!r}")


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def _parse_date(value: str, fmt: str) -> Optional[date]:
    """Return a date object or None; never raises."""
    try:
        return datetime.strptime(value.strip(), fmt).date()
    except (ValueError, AttributeError):
        return None


def _parse_anxiety_level(value: str) -> Optional[int]:
    try:
        lvl = int(value.strip())
        if 0 <= lvl <= 10:
            return lvl
        logger.warning("Anxiety level out of range (0-10): %s", value)
        return lvl          # keep the value but warn
    except ValueError:
        return None


def parse_demographics(source: str | io.StringIO) -> list[DemographicsRecord]:
    """
    Parse demographics CSV.

    `source` can be:
      - a file path string
      - a StringIO object (useful for Lambda / in-memory usage)
    """
    records: list[DemographicsRecord] = []
    reader = _csv_reader(source)

    for i, row in enumerate(reader):
        try:
            raw_hid = row["HID"]
            hid = normalize_hid(raw_hid)
        except (KeyError, ValueError) as exc:
            logger.warning("Row %d – skipping bad HID: %s", i, exc)
            continue

        records.append(DemographicsRecord(
            hid=hid,
            registration_date=_parse_date(row.get("Registration Date", ""), "%m-%d-%Y"),
            gender=row.get("Gender", "").strip(),
            race=row.get("Race#1", "").strip(),
            shelter=row.get("Shelter", "").strip(),
            date_of_birth=_parse_date(row.get("Date Of Birth", ""), "%m-%d-%Y"),
            _raw_first=row.get("First Name", ""),
            _raw_last=row.get("Last Name", ""),
        ))

    logger.info("Parsed %d demographics records", len(records))
    return records


def parse_anxiety(source: str | io.StringIO) -> list[EncounterRecord]:
    """Parse anxiety/encounter CSV."""
    records: list[EncounterRecord] = []
    reader = _csv_reader(source)

    for i, row in enumerate(reader):
        try:
            raw_hid = row["Homeless ID"]
            hid = normalize_hid(raw_hid)
        except (KeyError, ValueError) as exc:
            logger.warning("Row %d – skipping bad HID: %s", i, exc)
            continue

        records.append(EncounterRecord(
            hid=hid,
            encounter_date=_parse_date(row.get("Encounter Date", ""), "%Y-%m-%d"),
            anxiety_level=_parse_anxiety_level(row.get("Anxiety Lvl", "")),
        ))

    logger.info("Parsed %d encounter records", len(records))
    return records


# ---------------------------------------------------------------------------
# Join
# ---------------------------------------------------------------------------

def merge(
    demo_records: list[DemographicsRecord],
    encounter_records: list[EncounterRecord],
) -> tuple[list[MergedRecord], dict]:
    """
    Left-join encounters onto demographics using the canonical HID.

    Returns
    -------
    merged   : list of MergedRecord (one row per encounter)
    report   : diagnostic dict – unmatched HIDs on both sides, counts
    """
    demo_index: dict[str, DemographicsRecord] = {r.hid: r for r in demo_records}
    merged: list[MergedRecord] = []
    unmatched_encounters: list[str] = []

    for enc in encounter_records:
        demo = demo_index.get(enc.hid)
        if demo is None:
            logger.warning("Encounter HID %s has no demographics match", enc.hid)
            unmatched_encounters.append(enc.hid)
            continue

        merged.append(MergedRecord(
            hid=enc.hid,
            shelter=demo.shelter,
            gender=demo.gender,
            race=demo.race,
            date_of_birth=demo.date_of_birth,
            registration_date=demo.registration_date,
            encounter_date=enc.encounter_date,
            anxiety_level=enc.anxiety_level,
        ))

    encounter_hids = {r.hid for r in encounter_records}
    unmatched_demo = [r.hid for r in demo_records if r.hid not in encounter_hids]

    report = {
        "total_demographics": len(demo_records),
        "total_encounters": len(encounter_records),
        "merged_rows": len(merged),
        "unmatched_encounters": unmatched_encounters,
        "demographics_with_no_encounters": unmatched_demo,
    }
    logger.info("Merge report: %s", report)
    return merged, report


# ---------------------------------------------------------------------------
# Convenience: run the full pipeline from two file paths
# ---------------------------------------------------------------------------

def run_pipeline(
    demographics_path: str,
    anxiety_path: str,
) -> tuple[list[MergedRecord], dict]:
    """End-to-end: load → normalise → join → return."""
    demo = parse_demographics(demographics_path)
    encounters = parse_anxiety(anxiety_path)
    return merge(demo, encounters)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _csv_reader(source: str | io.StringIO) -> Iterator[dict]:
    """Yield rows as dicts whether source is a path or a StringIO."""
    if isinstance(source, str):
        with open(source, newline="", encoding="utf-8-sig") as fh:
            yield from csv.DictReader(fh)
    else:
        source.seek(0)
        yield from csv.DictReader(source)
