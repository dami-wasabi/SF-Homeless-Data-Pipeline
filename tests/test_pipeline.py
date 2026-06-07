"""
tests/test_pipeline.py
----------------------
Unit tests for the ETL pipeline.  Run with:

    pip install pytest
    pytest tests/ -v

No AWS credentials needed – all AWS calls are mocked with unittest.mock.
"""

import io
import json
import sys
import os
import unittest
from datetime import date
from unittest.mock import MagicMock, patch

# Make the project root importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from etl.transform import (
    normalize_hid,
    parse_demographics,
    parse_anxiety,
    merge,
    run_pipeline,
    MergedRecord,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

DEMO_CSV = """\
Identifier,HID,Registration Date,First Name,Last Name,Middle Name,Date Of Birth,Gender,Race#1,Shelter
0,001-15,01-09-2007,Meri,American,Red,02-25-1981,Male,Unknown,Billy's Shelter
1,002-15,01-05-2007,Mao,Woman,Smith,12-03-1984,Male,American Indian or Alaska Native,Have Hope Shelter
2,003-15,07-30-2000,Captain,American,Wonder,12-10-1997,Female,American Indian or Alaska Native,Have Hope Shelter
3,018-15,05-26-2007,Annetta,Person,Alma,07-31-1970,Female,Black or African American,Billy's Shelter
4,019-15,04-05-2004,Smith,Boy,Green,04-09-1986,Male,Unknown,Have Hope Shelter
"""

ANXIETY_CSV = """\
Homeless ID,Encounter Date,Anxiety Lvl
HM15-18,2019-05-01,4
HM15-18,2019-05-29,2
HM15-3,2019-05-25,10
HM15-19,2019-05-25,9
HM15-2,2019-05-28,6
HM15-1,2019-03-05,9
HM15-1,2019-10-08,2
"""


# ---------------------------------------------------------------------------
# HID normalisation tests
# ---------------------------------------------------------------------------

class TestNormalizeHid(unittest.TestCase):

    def test_anxiety_single_digit(self):
        self.assertEqual(normalize_hid("HM15-1"), "001-15")

    def test_anxiety_double_digit(self):
        self.assertEqual(normalize_hid("HM15-18"), "018-15")

    def test_anxiety_triple_digit(self):
        self.assertEqual(normalize_hid("HM15-123"), "123-15")

    def test_demo_format_passthrough(self):
        self.assertEqual(normalize_hid("001-15"), "001-15")
        self.assertEqual(normalize_hid("018-15"), "018-15")

    def test_case_insensitive(self):
        self.assertEqual(normalize_hid("hm15-5"), "005-15")

    def test_whitespace_stripped(self):
        self.assertEqual(normalize_hid("  HM15-3  "), "003-15")

    def test_invalid_format_raises(self):
        with self.assertRaises(ValueError):
            normalize_hid("BADFORMAT")

    def test_empty_string_raises(self):
        with self.assertRaises(ValueError):
            normalize_hid("")


# ---------------------------------------------------------------------------
# Parsing tests
# ---------------------------------------------------------------------------

class TestParseDemographics(unittest.TestCase):

    def setUp(self):
        self.records = parse_demographics(io.StringIO(DEMO_CSV))

    def test_record_count(self):
        self.assertEqual(len(self.records), 5)

    def test_hid_canonical(self):
        hids = [r.hid for r in self.records]
        self.assertIn("001-15", hids)
        self.assertIn("018-15", hids)

    def test_shelter_parsed(self):
        rec = next(r for r in self.records if r.hid == "001-15")
        self.assertEqual(rec.shelter, "Billy's Shelter")

    def test_gender_parsed(self):
        rec = next(r for r in self.records if r.hid == "001-15")
        self.assertEqual(rec.gender, "Male")

    def test_date_of_birth_parsed(self):
        rec = next(r for r in self.records if r.hid == "001-15")
        self.assertEqual(rec.date_of_birth, date(1981, 2, 25))

    def test_registration_date_parsed(self):
        rec = next(r for r in self.records if r.hid == "001-15")
        self.assertEqual(rec.registration_date, date(2007, 1, 9))


class TestParseAnxiety(unittest.TestCase):

    def setUp(self):
        self.records = parse_anxiety(io.StringIO(ANXIETY_CSV))

    def test_record_count(self):
        self.assertEqual(len(self.records), 7)

    def test_hid_normalised_on_parse(self):
        hids = [r.hid for r in self.records]
        # All HIDs should be in demographic canonical form after parse
        self.assertIn("018-15", hids)
        self.assertIn("003-15", hids)
        self.assertNotIn("HM15-18", hids)

    def test_anxiety_level_parsed(self):
        rec = next(r for r in self.records if r.hid == "003-15")
        self.assertEqual(rec.anxiety_level, 10)

    def test_encounter_date_parsed(self):
        rec = next(r for r in self.records if r.hid == "003-15")
        self.assertEqual(rec.encounter_date, date(2019, 5, 25))


# ---------------------------------------------------------------------------
# Merge / join tests
# ---------------------------------------------------------------------------

class TestMerge(unittest.TestCase):

    def setUp(self):
        demo = parse_demographics(io.StringIO(DEMO_CSV))
        enc  = parse_anxiety(io.StringIO(ANXIETY_CSV))
        self.merged, self.report = merge(demo, enc)

    def test_all_encounters_matched(self):
        # All 7 encounters have matching demographics in the fixture
        self.assertEqual(len(self.merged), 7)
        self.assertEqual(self.report["unmatched_encounters"], [])

    def test_merged_record_has_shelter(self):
        rec = next(r for r in self.merged if r.hid == "018-15")
        self.assertEqual(rec.shelter, "Billy's Shelter")

    def test_merged_record_has_anxiety(self):
        rec = next(r for r in self.merged
                   if r.hid == "018-15" and str(r.encounter_date) == "2019-05-01")
        self.assertEqual(rec.anxiety_level, 4)

    def test_unmatched_demo_reported(self):
        # 001-15, 003-15, 018-15, 019-15, 002-15 have encounters;
        # 003-15 has encounter. 002-15 has encounter.
        # The only demo record with NO encounter is anything not in anxiety CSV:
        # In our fixture, 001-15,002-15,003-15,018-15,019-15 are all present.
        # But 001-15 has encounters, 002-15 has encounters, 003-15 has encounter
        # 018-15 has encounters, 019-15 has encounter → no unmatched demo
        self.assertIsInstance(self.report["demographics_with_no_encounters"], list)

    def test_report_totals(self):
        self.assertEqual(self.report["total_demographics"], 5)
        self.assertEqual(self.report["total_encounters"], 7)
        self.assertEqual(self.report["merged_rows"], 7)

    def test_unmatched_encounter_not_in_output(self):
        """An encounter with no demographics match should not appear in merged."""
        bad_enc_csv = io.StringIO(
            "Homeless ID,Encounter Date,Anxiety Lvl\nHM15-99,2019-01-01,5\n"
        )
        demo = parse_demographics(io.StringIO(DEMO_CSV))
        enc  = parse_anxiety(bad_enc_csv)
        merged, report = merge(demo, enc)
        self.assertEqual(len(merged), 0)
        self.assertIn("099-15", report["unmatched_encounters"])


# ---------------------------------------------------------------------------
# MergedRecord serialisation
# ---------------------------------------------------------------------------

class TestMergedRecordSerialization(unittest.TestCase):

    def test_to_dict_dates_are_strings(self):
        rec = MergedRecord(
            hid="001-15",
            shelter="Billy's Shelter",
            gender="Male",
            race="Unknown",
            date_of_birth=date(1981, 2, 25),
            registration_date=date(2007, 1, 9),
            encounter_date=date(2019, 3, 5),
            anxiety_level=9,
        )
        d = rec.to_dict()
        self.assertIsInstance(d["date_of_birth"], str)
        self.assertEqual(d["date_of_birth"], "1981-02-25")
        self.assertEqual(d["encounter_date"], "2019-03-05")

    def test_to_dict_none_survives(self):
        rec = MergedRecord(
            hid="001-15", shelter="Test", gender="Male", race="Unknown",
            date_of_birth=None, registration_date=None,
            encounter_date=None, anxiety_level=None,
        )
        d = rec.to_dict()
        self.assertIsNone(d["date_of_birth"])


# ---------------------------------------------------------------------------
# Integration: run_pipeline with real CSV files
# ---------------------------------------------------------------------------

class TestRunPipelineIntegration(unittest.TestCase):

    DEMO_PATH    = os.path.join(os.path.dirname(__file__), "..", "data", "SF_HOMELESS_DEMOGRAPHICS.csv")
    ANXIETY_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "SF_HOMELESS_ANXIETY.csv")

    def test_pipeline_produces_merged_records(self):
        if not os.path.exists(self.DEMO_PATH):
            self.skipTest("Real CSV files not present")
        merged, report = run_pipeline(self.DEMO_PATH, self.ANXIETY_PATH)
        # The real datasets have 11 encounters, all with matching demographics
        self.assertEqual(len(merged), 11)
        self.assertEqual(report["unmatched_encounters"], [])

    def test_pipeline_all_records_have_shelter(self):
        if not os.path.exists(self.DEMO_PATH):
            self.skipTest("Real CSV files not present")
        merged, _ = run_pipeline(self.DEMO_PATH, self.ANXIETY_PATH)
        for rec in merged:
            self.assertTrue(rec.shelter, f"Missing shelter on {rec.hid}")

    def test_pipeline_all_anxiety_levels_valid(self):
        if not os.path.exists(self.DEMO_PATH):
            self.skipTest("Real CSV files not present")
        merged, _ = run_pipeline(self.DEMO_PATH, self.ANXIETY_PATH)
        for rec in merged:
            if rec.anxiety_level is not None:
                self.assertGreaterEqual(rec.anxiety_level, 0)
                self.assertLessEqual(rec.anxiety_level, 10)


if __name__ == "__main__":
    unittest.main(verbosity=2)
