import datetime as dt
import unittest

from calist import daymodel
from calist.models import time_from_minutes, fmt_time
from calist.store import DEFAULT_CONFIG, DEFAULT_STATE, sleep_hours


def cfg():
    import json
    return json.loads(json.dumps(DEFAULT_CONFIG))


def state():
    import json
    return json.loads(json.dumps(DEFAULT_STATE))


class TestRealisticDay(unittest.TestCase):
    def setUp(self):
        # Wednesday 2026-09-02 is a school day.
        self.wed = dt.date(2026, 9, 2)
        self.sat = dt.date(2026, 9, 5)

    def test_school_ends_at_240_but_work_starts_after_drive_and_settle(self):
        """The whole point: 14:40 + 17m drive + 15m settle => 15:12."""
        windows = daymodel.free_windows(self.wed, cfg(), state())
        after_school = [w for w in windows if w.start >= 14 * 60]
        self.assertTrue(after_school, "expected a work window after school")
        first = min(after_school, key=lambda w: w.start)
        self.assertEqual(fmt_time(time_from_minutes(first.start)), "15:12")

    def test_travel_before_school_is_also_removed(self):
        """Leaving for a 9:00 start means 8:40 is not free time."""
        windows = daymodel.free_windows(self.wed, cfg(), state())
        morning = [w for w in windows if w.start < 9 * 60]
        for w in morning:
            self.assertLessEqual(w.end, 8 * 60 + 40, "morning window must end by 08:40")

    def test_dinner_and_call_are_never_available(self):
        windows = daymodel.free_windows(self.wed, cfg(), state())
        dinner = (18 * 60 + 30, 19 * 60 + 15)
        call = (21 * 60, 21 * 60 + 45)
        for w in windows:
            for lo, hi in (dinner, call):
                self.assertFalse(
                    w.start < hi and w.end > lo,
                    f"window {w.label()} overlaps a protected anchor",
                )

    def test_gym_day_uses_earlier_cutoff_and_earlier_wake(self):
        bounds_gym = daymodel.day_bounds(self.wed, cfg(), state())
        self.assertEqual(fmt_time(time_from_minutes(bounds_gym.start)), "05:10")
        self.assertEqual(fmt_time(time_from_minutes(bounds_gym.end)), "22:00")

    def test_gym_habit_ramps_rather_than_starting_at_five_days(self):
        days = daymodel.gym_days_for(cfg(), state())
        self.assertEqual(len(days), 3, "new gym habit should start at 3x/week")
        advanced = state()
        advanced["gym_sessions_per_week"] = 5
        self.assertEqual(len(daymodel.gym_days_for(cfg(), advanced)), 5)

    def test_weekend_has_more_capacity_than_a_school_day(self):
        weekday_cap = daymodel.capacity_minutes(self.wed, cfg(), state())
        weekend_cap = daymodel.capacity_minutes(self.sat, cfg(), state())
        self.assertGreater(weekend_cap, weekday_cap)

    def test_now_minutes_clips_away_the_past(self):
        full = daymodel.capacity_minutes(self.wed, cfg(), state())
        late = daymodel.capacity_minutes(self.wed, cfg(), state(), now_minutes=20 * 60)
        self.assertLess(late, full)
        for w in daymodel.free_windows(self.wed, cfg(), state(), now_minutes=20 * 60):
            self.assertGreaterEqual(w.start, 20 * 60)

    def test_default_sleep_config_clears_the_seven_hour_line(self):
        self.assertGreaterEqual(sleep_hours("22:00", "05:10"), 7.0)
        self.assertGreaterEqual(sleep_hours("23:00", "07:00"), 7.0)


if __name__ == "__main__":
    unittest.main()
