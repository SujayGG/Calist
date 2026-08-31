import unittest

from calist import calibrate, habits


class TestCalibration(unittest.TestCase):
    def test_multiplier_reflects_consistent_overrun(self):
        records = [
            {"type": "done", "stage": "revise", "planned_minutes": 40, "actual_minutes": 60},
            {"type": "done", "stage": "revise", "planned_minutes": 40, "actual_minutes": 60},
            {"type": "done", "stage": "revise", "planned_minutes": 20, "actual_minutes": 30},
        ]
        self.assertEqual(calibrate.multipliers(records)["revise"], 1.5)

    def test_too_few_samples_do_not_calibrate(self):
        records = [{"type": "done", "stage": "draft", "planned_minutes": 60, "actual_minutes": 120}]
        self.assertNotIn("draft", calibrate.multipliers(records))

    def test_one_disaster_does_not_dominate(self):
        """Median, not mean - a single all-nighter must not warp every estimate."""
        records = [
            {"type": "done", "stage": "draft", "planned_minutes": 60, "actual_minutes": 60},
            {"type": "done", "stage": "draft", "planned_minutes": 60, "actual_minutes": 66},
            {"type": "done", "stage": "draft", "planned_minutes": 60, "actual_minutes": 60},
            {"type": "done", "stage": "draft", "planned_minutes": 60, "actual_minutes": 480},
        ]
        self.assertLessEqual(calibrate.multipliers(records)["draft"], 1.2)

    def test_multipliers_are_clamped(self):
        """A believable-but-extreme overrun still cannot blow past the clamp."""
        records = [
            {"type": "done", "stage": "polish", "planned_minutes": 30, "actual_minutes": 150}
        ] * 3
        self.assertEqual(calibrate.multipliers(records)["polish"], calibrate.CLAMP_HIGH)

    def test_implausible_ratios_are_rejected_as_bad_data(self):
        records = [
            {"type": "done", "stage": "polish", "planned_minutes": 10, "actual_minutes": 3000}
        ] * 3
        self.assertNotIn("polish", calibrate.multipliers(records))


class TestHabits(unittest.TestCase):
    def test_session_minutes_land_in_the_right_hours(self):
        records = [
            {"ts": "2026-08-30T21:00:00", "app": "instagram", "event": "open"},
            {"ts": "2026-08-30T22:30:00", "app": "instagram", "event": "close"},
        ]
        by_hour = habits.usage_minutes_by_hour(records)
        self.assertAlmostEqual(by_hour.get(21, 0), 60.0, places=1)
        self.assertAlmostEqual(by_hour.get(22, 0), 30.0, places=1)

    def test_non_social_apps_are_ignored(self):
        records = [
            {"ts": "2026-08-30T21:00:00", "app": "com.google.calendar", "event": "open"},
            {"ts": "2026-08-30T22:00:00", "app": "com.google.calendar", "event": "close"},
        ]
        self.assertEqual(habits.usage_minutes_by_hour(records), {})

    def test_manual_totals_are_accepted(self):
        records = [{"date": "2026-08-30", "app": "instagram", "minutes": 120, "hours": [20, 21]}]
        by_hour = habits.usage_minutes_by_hour(records)
        self.assertEqual(by_hour[20], 60.0)
        self.assertEqual(by_hour[21], 60.0)

    def test_follow_through_needs_enough_samples(self):
        records = [
            {"type": "done", "scheduled_hour": 16},
            {"type": "done", "scheduled_hour": 16},
            {"type": "skip", "scheduled_hour": 16},
            {"type": "done", "scheduled_hour": 7},
        ]
        scores = habits.follow_through_by_hour(records)
        self.assertAlmostEqual(scores[16], 0.67, places=2)
        self.assertNotIn(7, scores, "one sample is not evidence")


if __name__ == "__main__":
    unittest.main()
