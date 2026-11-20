import io
import unittest
from datetime import date

from srtrack.io import export_csv, import_csv
from srtrack.store import SRError, Store, normalize_severity, normalize_status, parse_date


class ParsingTests(unittest.TestCase):
    def test_parse_date(self):
        today = date(2026, 11, 20)  # a Friday
        self.assertEqual(parse_date("today", today), "2026-11-20")
        self.assertEqual(parse_date("tomorrow", today), "2026-11-21")
        self.assertEqual(parse_date("+3d", today), "2026-11-23")
        self.assertEqual(parse_date("2w", today), "2026-12-04")
        self.assertEqual(parse_date("-1d", today), "2026-11-19")
        self.assertEqual(parse_date("mon", today), "2026-11-23")
        self.assertEqual(parse_date("fri", today), "2026-11-27")
        self.assertEqual(parse_date("2026-12-01", today), "2026-12-01")
        self.assertIsNone(parse_date("none", today))
        with self.assertRaises(SRError):
            parse_date("someday", today)

    def test_status_and_severity(self):
        self.assertEqual(normalize_status("cus"), "CUS")
        self.assertEqual(normalize_status("Work In Progress"), "WIP")
        self.assertEqual(normalize_status("closed"), "CLO")
        with self.assertRaises(SRError):
            normalize_status("XYZ")
        self.assertEqual(normalize_severity("Sev 2"), 2)
        self.assertEqual(normalize_severity("S1"), 1)
        with self.assertRaises(SRError):
            normalize_severity(5)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")
        self.store.add("3-38123456789", "ORA-600", severity=1, product="DB 19c", tags="prod")
        self.store.add("3-38120001111", "EBS hang", severity=2, status="WIP", follow_up="2000-01-01")

    def tearDown(self):
        self.store.close()

    def test_resolve_suffix(self):
        self.assertEqual(self.store.resolve("6789"), "3-38123456789")
        self.assertEqual(self.store.resolve("3-38120001111"), "3-38120001111")
        with self.assertRaises(SRError):
            self.store.resolve("424242")

    def test_ambiguous_suffix(self):
        self.store.add("3-38100006789", "other", severity=3)
        with self.assertRaisesRegex(SRError, "ambiguous"):
            self.store.resolve("6789")

    def test_duplicate(self):
        with self.assertRaises(SRError):
            self.store.add("3-38123456789", "dup")

    def test_update_records_history_and_status_time(self):
        sr, changes = self.store.update("6789", status="CUS", severity=2)
        self.assertEqual(sr.status, "CUS")
        self.assertEqual(sr.waiting_on, "us")
        self.assertEqual(len(changes), 2)
        kinds = [e.kind for e in self.store.events("6789")]
        self.assertEqual(kinds, ["created", "change"])
        # no-op update produces no event
        _, changes = self.store.update("6789", status="CUS")
        self.assertEqual(changes, [])
        self.assertEqual(len(self.store.events("6789")), 2)

    def test_close_and_reopen(self):
        sr, _ = self.store.update("1111", status="CLO")
        self.assertFalse(sr.is_open)
        self.assertIsNotNone(sr.closed_at)
        self.assertEqual([s.number for s in self.store.query()], ["3-38123456789"])
        sr, _ = self.store.update("1111", status="WIP")
        self.assertIsNone(sr.closed_at)

    def test_query_filters(self):
        self.assertEqual(len(self.store.query(severities=["1"])), 1)
        self.assertEqual(len(self.store.query(waiting_on="oracle")), 2)
        self.assertEqual(len(self.store.query(tag="prod")), 1)
        self.assertEqual(len(self.store.query(tag="pro")), 0)
        self.assertEqual(len(self.store.query(due_by=date.today().isoformat())), 1)
        self.assertEqual(self.store.get("1111").is_overdue(), True)

    def test_search_includes_notes(self):
        self.store.add_note("1111", "Engineer requested AWR report")
        res = self.store.query(text="awr")
        self.assertEqual([s.number for s in res], ["3-38120001111"])

    def test_tags_and_bugs(self):
        sr = self.store.add_tags("6789", ["rac", "prod"])
        self.assertEqual(sr.tag_list, ["prod", "rac"])
        sr = self.store.add_tags("6789", ["prod"], remove=True)
        self.assertEqual(sr.tag_list, ["rac"])
        sr = self.store.link_bugs("6789", ["35123456"])
        self.assertEqual(sr.bug_list, ["35123456"])

    def test_delete_cascades(self):
        self.store.add_note("6789", "x")
        self.store.delete("6789")
        self.assertEqual(
            self.store.conn.execute("SELECT COUNT(*) FROM event WHERE sr_number='3-38123456789'").fetchone()[0],
            0,
        )

    def test_csv_roundtrip(self):
        buf = io.StringIO()
        export_csv(self.store.all(), buf)
        buf.seek(0)
        other = Store(":memory:")
        added, updated, errors = import_csv(other, buf)
        self.assertEqual((added, updated, errors), (2, 0, []))
        self.assertEqual(other.get("6789").severity, 1)

    def test_csv_import_aliases_and_errors(self):
        data = "SR Number,Problem Summary,Sev,Status\n3-40000000001,New issue,2,WIP\nbad,x,1,NEW\n"
        added, _, errors = import_csv(self.store, io.StringIO(data))
        self.assertEqual(added, 1)
        self.assertEqual(len(errors), 1)
        self.assertEqual(self.store.get("0001").status, "WIP")


if __name__ == "__main__":
    unittest.main()
