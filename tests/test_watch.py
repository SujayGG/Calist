import unittest

from calist.watch import DwellTracker
from calist.watch.backends import FakeBackend, matches_watchlist

MIN = 60.0
PROCESSES = ["WhatsApp.exe", "Discord.exe"]
PATTERNS = [r"(?i)instagram", r"(?i)tiktok", r"(?i)youtube"]


class TestWatchlistMatching(unittest.TestCase):
    def test_desktop_app_matches_by_process_name(self):
        self.assertEqual(
            matches_watchlist("WhatsApp.exe", "Chats", PROCESSES, PATTERNS), "WhatsApp.exe"
        )

    def test_instagram_in_a_browser_tab_matches_by_title(self):
        """Instagram is a browser tab, not a process - titles must be checked."""
        label = matches_watchlist("chrome.exe", "Instagram - Google Chrome", PROCESSES, PATTERNS)
        self.assertTrue(label)

    def test_ordinary_work_does_not_match(self):
        self.assertEqual(
            matches_watchlist("Code.exe", "essay.docx - Word", PROCESSES, PATTERNS), ""
        )

    def test_a_broken_regex_does_not_crash_the_watcher(self):
        self.assertEqual(matches_watchlist("chrome.exe", "x", [], ["([unclosed"]), "")


class TestDwellTracker(unittest.TestCase):
    def setUp(self):
        self.t = DwellTracker(dwell_minutes=7, cooldown_minutes=20)

    def test_no_nudge_before_the_dwell_time(self):
        for minute in range(0, 7):
            self.assertFalse(self.t.tick(minute * MIN, "instagram"))

    def test_nudge_fires_once_the_dwell_time_passes(self):
        self.t.tick(0, "instagram")
        self.assertTrue(self.t.tick(7 * MIN, "instagram"))

    def test_switching_apps_resets_the_clock(self):
        self.t.tick(0, "instagram")
        self.t.tick(5 * MIN, "whatsapp")
        self.assertFalse(self.t.tick(9 * MIN, "whatsapp"),
                         "only 4 minutes in whatsapp - too early")
        self.assertTrue(self.t.tick(12 * MIN, "whatsapp"))

    def test_leaving_the_app_resets_the_clock(self):
        self.t.tick(0, "instagram")
        self.t.tick(5 * MIN, "")
        self.t.tick(6 * MIN, "instagram")
        self.assertFalse(self.t.tick(11 * MIN, "instagram"))

    def test_cooldown_prevents_nagging(self):
        self.t.tick(0, "instagram")
        self.assertTrue(self.t.tick(7 * MIN, "instagram"))
        self.assertFalse(self.t.tick(15 * MIN, "instagram"), "within the 20 min cooldown")
        self.assertTrue(self.t.tick(35 * MIN, "instagram"))

    def test_snooze_silences_everything_until_it_expires(self):
        self.t.tick(0, "instagram")
        self.assertTrue(self.t.tick(7 * MIN, "instagram"))
        self.t.snooze(7 * MIN, 5)          # quiet until t=12
        self.t.last_nudge.clear()          # isolate snooze from the cooldown rule
        self.assertFalse(self.t.tick(10 * MIN, "instagram"), "inside the snooze window")
        self.assertTrue(self.t.tick(20 * MIN, "instagram"), "snooze expired at t=12")

    def test_snooze_does_not_outlast_its_window(self):
        self.t.snooze(0, 5)
        self.t.tick(0, "instagram")
        self.assertFalse(self.t.tick(4 * MIN, "instagram"))
        self.assertTrue(self.t.tick(8 * MIN, "instagram"))

    def test_a_watched_app_left_open_all_evening_nudges_repeatedly(self):
        fires = 0
        self.t.tick(0, "instagram")
        for minute in range(1, 121):
            if self.t.tick(minute * MIN, "instagram"):
                fires += 1
        self.assertGreaterEqual(fires, 3)
        self.assertLessEqual(fires, 6, "should not nag every poll")


class TestBackend(unittest.TestCase):
    def test_fake_backend_replays_a_sequence_then_holds(self):
        b = FakeBackend([("chrome.exe", "Instagram"), ("Code.exe", "essay")])
        self.assertEqual(b.current(), ("chrome.exe", "Instagram"))
        self.assertEqual(b.current(), ("Code.exe", "essay"))
        self.assertEqual(b.current(), ("Code.exe", "essay"))


if __name__ == "__main__":
    unittest.main()


class TestDryRunDisplay(unittest.TestCase):
    def test_dwell_display_handles_a_zero_timestamp(self):
        """A clock starting at 0 is valid; 0 must not be treated as 'unset'."""
        t = DwellTracker(dwell_minutes=7)
        t.tick(0.0, "instagram")
        self.assertEqual(t.since, 0.0)
        base = t.since if t.since is not None else 300.0
        self.assertEqual(int((300.0 - base) // 60), 5)
