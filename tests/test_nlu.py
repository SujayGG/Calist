import datetime as dt
import unittest

from calist import nlu
from calist.models import Task
from calist.tasking import make_task
from tests.helpers import cfg

TODAY = dt.date(2026, 8, 31)   # a Monday


def sample_tasks(c):
    tasks = []
    for school in ("Purdue", "Stanford"):
        for i in (1, 2, 3):
            tasks.append(make_task(f"{school} - essay {i}", tasks, c,
                                   kind="essay", due="2026-11-01"))
    tasks.append(make_task("Physics - Friction Lab", tasks, c,
                           kind="schoolwork", due="2026-08-31", estimate=60))
    return tasks


class TestDates(unittest.TestCase):
    def test_common_forms(self):
        cases = {
            "sept 20": dt.date(2026, 9, 20),
            "september 20th": dt.date(2026, 9, 20),
            "9/20": dt.date(2026, 9, 20),
            "2026-09-20": dt.date(2026, 9, 20),
            "20 sep": dt.date(2026, 9, 20),
            "the 14th": dt.date(2026, 9, 14),
            "today": TODAY,
            "tomorrow": dt.date(2026, 9, 1),
        }
        for text, want in cases.items():
            self.assertEqual(nlu.parse_date(text, TODAY), want, text)

    def test_weekday_always_looks_forward(self):
        # TODAY is a Monday; "friday" means this week's Friday
        self.assertEqual(nlu.parse_date("friday", TODAY), dt.date(2026, 9, 4))
        # the same weekday means next week, never today
        self.assertEqual(nlu.parse_date("monday", TODAY), dt.date(2026, 9, 7))

    def test_bare_day_of_month_rolls_forward(self):
        """'the 5th' on the 31st means next month, not four weeks ago."""
        self.assertEqual(nlu.parse_date("the 5th", TODAY), dt.date(2026, 9, 5))

    def test_nonsense_returns_none(self):
        self.assertIsNone(nlu.parse_date("sometime soonish", TODAY))
        self.assertIsNone(nlu.parse_date("feb 31", TODAY))

    def test_durations(self):
        self.assertEqual(nlu.parse_minutes("2 hours"), 120)
        self.assertEqual(nlu.parse_minutes("90 min"), 90)
        self.assertEqual(nlu.parse_minutes("1.5 hours"), 90)
        self.assertEqual(nlu.parse_minutes("an hour and a half"), 90)
        self.assertEqual(nlu.parse_minutes("an hour"), 60)
        self.assertIsNone(nlu.parse_minutes("a while"))


class TestRulesParser(unittest.TestCase):
    """The rules path must cover everyday phrasing with no model running."""

    def setUp(self):
        self.c = cfg()
        self.tasks = sample_tasks(self.c)

    def parse(self, text):
        return nlu.parse_rules(text, TODAY, self.tasks)

    def test_add_with_date_and_duration(self):
        cmd = self.parse("add AP Bio unit 3 test on the 14th, 4 hours")
        self.assertEqual(cmd.action, "add")
        self.assertEqual(cmd.params["title"], "AP Bio unit 3 test")
        self.assertEqual(cmd.params["kind"], "test")
        self.assertEqual(cmd.params["due"], "2026-09-14")
        self.assertEqual(cmd.params["estimate"], 240)

    def test_add_infers_essay_by_default(self):
        cmd = self.parse("add Purdue why this major due sept 20 2 hours")
        self.assertEqual(cmd.params["kind"], "essay")
        self.assertEqual(cmd.params["due"], "2026-09-20")
        self.assertNotIn("sept", cmd.params["title"].lower())
        self.assertNotIn("2 hours", cmd.params["title"].lower())

    def test_done_with_actual_minutes(self):
        cmd = self.parse("done purdue essay 1, took 90 minutes")
        self.assertEqual(cmd.action, "done")
        self.assertEqual(cmd.params["minutes"], 90)

    def test_natural_done_phrasing(self):
        cmd = self.parse("finished the Purdue essay 2, took me like an hour and a half")
        self.assertEqual(cmd.action, "done")
        self.assertEqual(cmd.params["minutes"], 90)

    def test_skip_move_block_replan(self):
        self.assertEqual(self.parse("skip the friction lab").action, "skip")
        move = self.parse("move stanford essay 1 to friday")
        self.assertEqual(move.params["to"], "2026-09-04")
        blk = self.parse("block sept 11 - sept 15")
        self.assertEqual((blk.params["from"], blk.params["to"]),
                         ("2026-09-11", "2026-09-15"))
        self.assertEqual(self.parse("replan").action, "replan")

    def test_unrecognised_returns_none_rather_than_guessing(self):
        self.assertIsNone(self.parse("hmm what should I do about wednesdays"))

    def test_move_without_a_readable_date_is_refused(self):
        with self.assertRaises(nlu.ParseError):
            self.parse("move purdue essay 1 to whenever")


class TestMatching(unittest.TestCase):
    def setUp(self):
        self.c = cfg()
        self.tasks = sample_tasks(self.c)

    def test_numbered_essays_resolve_exactly(self):
        got = nlu.resolve_task("purdue essay 1", self.tasks)
        self.assertEqual([t.title for t in got], ["Purdue - essay 1"])

    def test_ambiguity_is_reported_not_guessed(self):
        got = nlu.resolve_task("purdue essay", self.tasks)
        self.assertEqual(len(got), 3)

    def test_no_match_is_empty(self):
        self.assertEqual(nlu.resolve_task("chemistry olympiad", self.tasks), [])

    def test_finished_tasks_are_not_candidates(self):
        for s in self.tasks[0].stages:
            s.status = "done"
        got = nlu.resolve_task("purdue essay 1", self.tasks)
        self.assertNotIn("Purdue - essay 1", [t.title for t in got])

    def test_a_school_name_is_never_ignored(self):
        """'purdue essay 1' must not fall through to a Stanford essay."""
        for s in self.tasks[0].stages:
            s.status = "done"
        for t in nlu.resolve_task("purdue essay 1", self.tasks):
            self.assertEqual(t.school, "Purdue", f"matched {t.title}")


class TestModelSafety(unittest.TestCase):
    """The model proposes; validation decides. Nothing unvalidated is applied."""

    def test_json_is_extracted_from_fenced_output(self):
        obj = nlu.extract_json('```json\n{"action":"replan"}\n```')
        self.assertEqual(obj["action"], "replan")

    def test_prose_around_json_is_tolerated(self):
        obj = nlu.extract_json('Sure! {"action":"replan"} hope that helps')
        self.assertEqual(obj["action"], "replan")

    def test_non_json_is_rejected(self):
        for bad in ("I think you should rest", "{not json", ""):
            with self.assertRaises(nlu.ParseError):
                nlu.extract_json(bad)

    def test_unknown_action_is_refused(self):
        for bad in ({"action": "delete_everything"}, {"action": "unknown"}, {}):
            with self.assertRaises(nlu.ParseError):
                nlu.validate(bad, TODAY)

    def test_malformed_dates_are_refused(self):
        with self.assertRaises(nlu.ParseError):
            nlu.validate({"action": "move", "task": "x", "to": "next tuesday"}, TODAY)
        with self.assertRaises(nlu.ParseError):
            nlu.validate({"action": "add", "title": "x", "due": "2026-13-45"}, TODAY)

    def test_add_requires_a_title(self):
        with self.assertRaises(nlu.ParseError):
            nlu.validate({"action": "add", "title": "  "}, TODAY)

    def test_absurd_estimates_are_clamped(self):
        cmd = nlu.validate({"action": "add", "title": "x", "estimate": 100000}, TODAY)
        self.assertLessEqual(cmd.params["estimate"], 8 * 60)

    def test_anchor_times_must_be_times(self):
        with self.assertRaises(nlu.ParseError):
            nlu.validate({"action": "anchor", "id": "school", "start": "afternoon"}, TODAY)
        cmd = nlu.validate({"action": "anchor", "id": "school", "end": "14:15"}, TODAY)
        self.assertEqual(cmd.params["end"], "14:15")

    def test_backwards_blackout_range_is_corrected(self):
        cmd = nlu.validate({"action": "blackout", "from": "2026-09-15",
                            "to": "2026-09-11"}, TODAY)
        self.assertEqual((cmd.params["from"], cmd.params["to"]),
                         ("2026-09-11", "2026-09-15"))

    def test_model_is_never_called_when_rules_match(self):
        c = cfg()
        c["nlu"] = {"endpoint": "http://127.0.0.1:9/never"}   # would fail if used
        cmd = nlu.parse("replan", c, sample_tasks(c))
        self.assertEqual(cmd.action, "replan")
        self.assertEqual(cmd.source, "rules")

    def test_rules_only_mode_never_reaches_the_network(self):
        c = cfg()
        c["nlu"] = {"endpoint": "http://127.0.0.1:9/never"}
        with self.assertRaises(nlu.ParseError):
            nlu.parse("please reorganise my whole life", c, sample_tasks(c), use_model=False)


if __name__ == "__main__":
    unittest.main()


class TestPlatformPortability(unittest.TestCase):
    """`calist say` crashed on Windows: %-d is a glibc-only strftime flag."""

    def test_fmt_day_needs_no_platform_specific_flag(self):
        from calist.models import fmt_day
        self.assertEqual(fmt_day(dt.date(2026, 9, 4)), "Fri Sep 4")
        self.assertEqual(fmt_day(dt.date(2026, 9, 14)), "Mon Sep 14")

    def test_no_glibc_only_format_flags_in_the_package(self):
        import pathlib
        import re
        root = pathlib.Path(__file__).resolve().parent.parent / "calist"
        offenders = []
        for path in root.rglob("*.py"):
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if line.lstrip().startswith("#") or '"""' in line:
                    continue
                if re.search(r'strftime\([^)]*%[-#]', line):
                    offenders.append(f"{path.name}:{n}")
        self.assertEqual(offenders, [], "platform-specific strftime flags")

    def test_console_output_stays_ascii(self):
        """Windows consoles are not always UTF-8; keep printed text plain."""
        import pathlib
        src = (pathlib.Path(__file__).resolve().parent.parent
               / "calist" / "nlu.py").read_text(encoding="utf-8")
        bad = sorted({c for c in src if ord(c) > 127})
        self.assertEqual(bad, [], f"non-ASCII in nlu.py: {bad}")

    def test_every_command_renders_a_summary(self):
        """Each describe() branch that formats a date must survive on Windows."""
        c = cfg()
        tasks = sample_tasks(c)
        for text in ("add Rice supplement due sept 20, 2 hours",
                     "move stanford essay 1 to friday",
                     "block sept 11 - sept 15",
                     "done purdue essay 1, took 90 min",
                     "replan"):
            cmd = nlu.parse_rules(text, TODAY, tasks)
            summary = nlu.describe(cmd, tasks, c)["summary"]
            self.assertTrue(summary.strip(), text)
            self.assertTrue(summary.isascii(), f"non-ascii summary for {text!r}")
