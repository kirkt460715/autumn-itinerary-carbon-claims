import csv
from pathlib import Path
import unittest

DATA = Path(__file__).resolve().parents[1] / "data"

TABLES = {
    'factors': ('factor version region mode valid_from valid_through rate_low rate_high documented_at', 'factor version'),
    'legs': ('leg vehicle_run day region mode kilometers_lo kilometers_hi linked_leg basis received_on', 'leg'),
    'boardings': ('leg tour children adults wheelchair basis', 'leg tour'),
    'offers': ('offer route legs tour example_size minimum_size incoming outgoing accessibility sell_from sell_until', 'offer'),
    'benchmarks': ('reference event_key region vehicle mode kilometers_low kilometers_high size incoming outgoing access active_since shared_legs', 'reference'),
    'changes': ('notice offer action tour affected_leg effective_at received_at', 'notice'),
    'labels': ('label offer reference minimum_saving state issued_at valid_window', 'label'),
    'cards': ('card edition offer parent_card cohort state active_from active_until', 'card'),
}


class SourceDataTests(unittest.TestCase):
    def load(self, name):
        fields, _ = TABLES[name]
        with (DATA / (name + ".csv")).open(encoding="utf-8", newline="") as handle:
            contents = csv.DictReader(handle)
            self.assertEqual(contents.fieldnames, fields.split())
            records = list(contents)
        self.assertTrue(records)
        self.assertTrue(all(None not in row and None not in row.values() for row in records))
        return records

    def test_format_and_record_keys(self):
        for name, (_, key) in TABLES.items():
            with self.subTest(table=name):
                records = self.load(name)
                values = [tuple(row[field] for field in key.split()) for row in records]
                self.assertTrue(all(all(value) for value in values))
                self.assertEqual(len(values), len(set(values)))

    def test_related_source_records(self):
        rows = {name: self.load(name) for name in TABLES}
        self.assertTrue(any(r["linked_leg"] for r in rows["legs"]))
        self.assertTrue(any(not r["kilometers_hi"] for r in rows["legs"]))
        self.assertGreater(len(rows["boardings"]), len({r["leg"] for r in rows["boardings"]}))
        self.assertTrue(any(r["action"] == "cancelled" for r in rows["changes"]))
        self.assertTrue(any(r["state"] == "active" for r in rows["labels"]))
