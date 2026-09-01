import datetime as dt
import unittest

from calist import clock


class TestLocalClock(unittest.TestCase):
    """Plans are generated both on his machine and in a UTC container; a plan
    built after midnight UTC must not be a day ahead of him."""

    CENTRAL = {"timezone": {"standard_offset_hours": -6, "us_dst": True}}

    def test_us_dst_boundaries(self):
        # 2026: DST starts Sun Mar 8, ends Sun Nov 1
        self.assertFalse(clock.us_dst_active(dt.datetime(2026, 3, 8, 1, 59)))
        self.assertTrue(clock.us_dst_active(dt.datetime(2026, 3, 8, 2, 1)))
        self.assertTrue(clock.us_dst_active(dt.datetime(2026, 10, 31, 12)))
        self.assertFalse(clock.us_dst_active(dt.datetime(2026, 11, 1, 2, 1)))

    def test_offset_is_minus_five_in_september(self):
        self.assertEqual(clock.offset_hours(self.CENTRAL), -5.0)

    def test_offset_can_be_pinned_without_dst(self):
        cfg = {"timezone": {"standard_offset_hours": -6, "us_dst": False}}
        self.assertEqual(clock.offset_hours(cfg), -6.0)

    def test_local_time_is_derived_from_utc_not_the_host_clock(self):
        now = clock.now(self.CENTRAL)
        utc = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
        delta = (utc - now).total_seconds() / 3600
        self.assertAlmostEqual(delta, 5.0, places=1)

    def test_today_matches_that_local_time(self):
        self.assertEqual(clock.today(self.CENTRAL), clock.now(self.CENTRAL).date())

    def test_minutes_now_is_within_a_day(self):
        m = clock.minutes_now(self.CENTRAL)
        self.assertGreaterEqual(m, 0)
        self.assertLess(m, 24 * 60)


if __name__ == "__main__":
    unittest.main()
