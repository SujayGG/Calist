import datetime as dt
import tempfile
import unittest
from pathlib import Path

from calist import icsio
from calist.models import Block


class TestIcsRoundTrip(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()) / "plan.ics"
        self.blocks = [
            Block(date="2026-09-02", start="15:12", end="16:18",
                  title="Draft: Purdue - why, this major", type="work", note="Purdue"),
            Block(date="2026-09-02", start="16:28", end="17:10",
                  title="Revise: Indiana", type="work"),
        ]

    def test_written_events_parse_back_identically(self):
        icsio.write_ics(self.blocks, self.tmp)
        events = icsio.read_ics(self.tmp)
        self.assertEqual(len(events), 2)
        first = events[0]
        self.assertEqual(first["summary"], "Draft: Purdue - why, this major")
        self.assertEqual(first["dtstart"], dt.datetime(2026, 9, 2, 15, 12))
        self.assertEqual(first["dtend"], dt.datetime(2026, 9, 2, 16, 18))

    def test_commas_and_semicolons_survive_escaping(self):
        blocks = [Block(date="2026-09-02", start="09:00", end="10:00",
                        title="Essay: one, two; three\\four")]
        icsio.write_ics(blocks, self.tmp)
        self.assertEqual(icsio.read_ics(self.tmp)[0]["summary"], "Essay: one, two; three\\four")

    def test_output_uses_crlf_and_required_properties(self):
        icsio.write_ics(self.blocks, self.tmp)
        raw = self.tmp.read_bytes()
        self.assertIn(b"\r\n", raw)
        self.assertNotIn(b"\n\n", raw)
        text = raw.decode()
        for prop in ("BEGIN:VCALENDAR", "VERSION:2.0", "UID:", "DTSTAMP:", "END:VCALENDAR"):
            self.assertIn(prop, text)

    def test_times_are_floating_so_they_read_as_local(self):
        icsio.write_ics(self.blocks, self.tmp)
        text = self.tmp.read_bytes().decode()
        self.assertIn("DTSTART:20260902T151200", text)
        self.assertNotIn("DTSTART:20260902T151200Z", text)
        self.assertNotIn("TZID", text)

    def test_long_summaries_are_folded_and_unfold_cleanly(self):
        blocks = [Block(date="2026-09-02", start="09:00", end="10:00", title="X" * 200)]
        icsio.write_ics(blocks, self.tmp)
        # read_bytes: read_text() would translate CRLF to LF and hide the folding
        for line in self.tmp.read_bytes().decode().split("\r\n"):
            self.assertLessEqual(len(line), 75)
        self.assertEqual(icsio.read_ics(self.tmp)[0]["summary"], "X" * 200)

    def test_uid_is_stable_across_rewrites(self):
        icsio.write_ics(self.blocks, self.tmp)
        first = [e["uid"] for e in icsio.read_ics(self.tmp)]
        icsio.write_ics(self.blocks, self.tmp)
        self.assertEqual(first, [e["uid"] for e in icsio.read_ics(self.tmp)])


class TestIcsImport(unittest.TestCase):
    def test_weekly_recurring_event_becomes_an_anchor_spec(self):
        raw = (
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\n"
            "UID:x@y\r\nSUMMARY:Robotics Club\r\n"
            "DTSTART:20260903T153000\r\nDTEND:20260903T170000\r\n"
            "RRULE:FREQ=WEEKLY;BYDAY=TU,TH\r\n"
            "END:VEVENT\r\nEND:VCALENDAR\r\n"
        )
        path = Path(tempfile.mkdtemp()) / "club.ics"
        path.write_text(raw)
        specs = icsio.events_to_anchor_specs(icsio.read_ics(path))
        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0]["days"], ["tue", "thu"])
        self.assertEqual(specs[0]["start"], "15:30")
        self.assertEqual(specs[0]["end"], "17:00")


if __name__ == "__main__":
    unittest.main()
