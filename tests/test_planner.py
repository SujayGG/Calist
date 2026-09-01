import datetime as dt
import unittest

from calist import daymodel, planner
from calist.models import minutes_of, parse_time
from calist.tasking import make_task, stages_for
from tests.helpers import blocks_on, cfg, essays, state

TODAY = dt.date(2026, 8, 31)  # a Monday


class TestCoachLoop(unittest.TestCase):
    def test_revision_is_not_scheduled_before_the_coach_returns_it(self):
        """The coach gate: draft on day X, revise no earlier than X + latency."""
        c = cfg()
        tasks = essays(4, dt.date(2026, 9, 25), c)
        result = planner.plan(tasks, c, state(), today=TODAY)

        drafts, revises = {}, {}
        for b in result.blocks:
            if b.stage_name == "draft":
                drafts.setdefault(b.task_id, b.date)
            if b.stage_name == "revise-1":
                revises.setdefault(b.task_id, b.date)

        latency = c["coach_latency_days"]
        checked = 0
        for task_id, revise_date in revises.items():
            self.assertIn(task_id, drafts, "a revision was scheduled with no draft")
            gap = (dt.date.fromisoformat(revise_date) - dt.date.fromisoformat(drafts[task_id])).days
            self.assertGreaterEqual(
                gap, latency,
                f"{task_id} revised {gap}d after drafting, coach needs {latency}d",
            )
            checked += 1
        self.assertGreater(checked, 0, "expected at least one draft->revise pair")

    def test_completed_draft_gates_the_revision_from_its_real_done_date(self):
        c = cfg()
        task = make_task("Purdue supplement", [], c, kind="essay", due="2026-09-20")
        task.stages[0].status = "done"
        task.stages[0].done_date = TODAY.isoformat()
        result = planner.plan([task], c, state(), today=TODAY)
        revise = next(b for b in result.blocks if b.stage_name == "revise-1")
        gap = (dt.date.fromisoformat(revise.date) - TODAY).days
        self.assertGreaterEqual(gap, c["coach_latency_days"])


class TestCadence(unittest.TestCase):
    def test_at_most_one_new_draft_per_day(self):
        """Spare capacity must not become five drafts the coach cannot absorb."""
        c = cfg()
        tasks = essays(20, dt.date(2026, 10, 15), c)
        result = planner.plan(tasks, c, state(), today=TODAY)
        per_day = {}
        for b in result.blocks:
            if b.stage_name == "draft":
                per_day.setdefault(b.date, set()).add(b.task_id)
        for day, ids in per_day.items():
            self.assertLessEqual(len(ids), 1, f"{len(ids)} drafts on {day}")

    def test_twenty_essays_actually_get_drafted_within_the_horizon(self):
        c = cfg()
        tasks = essays(20, dt.date(2026, 10, 15), c)
        result = planner.plan(tasks, c, state(), today=TODAY)
        drafted = {b.task_id for b in result.blocks if b.stage_name == "draft"}
        self.assertEqual(len(drafted), 20, "every essay should get a draft scheduled")


class TestPriorityAndProtection(unittest.TestCase):
    def test_schoolwork_is_scheduled_before_essays_on_the_same_day(self):
        c = cfg()
        tasks = essays(3, dt.date(2026, 9, 30), c)
        hw = make_task("AP Gov reading", tasks, c, kind="schoolwork",
                       due=(TODAY + dt.timedelta(days=1)).isoformat(), estimate=45)
        tasks.append(hw)
        result = planner.plan(tasks, c, state(), today=TODAY)
        first_day = blocks_on(result, TODAY)
        kinds = [b.kind for b in first_day if b.kind in ("schoolwork", "essay")]
        if "schoolwork" in kinds and "essay" in kinds:
            self.assertLess(kinds.index("schoolwork"), kinds.index("essay"))

    def test_essays_keep_a_floor_even_under_a_pile_of_schoolwork(self):
        """Schoolwork must not crowd essays out for weeks and blow the target."""
        c = cfg()
        tasks = []
        for i in range(8):
            tasks.append(make_task(f"Homework {i}", tasks, c, kind="schoolwork",
                                   due=(TODAY + dt.timedelta(days=1)).isoformat(), estimate=90))
        tasks.extend(essays(5, dt.date(2026, 9, 25), c))
        result = planner.plan(tasks, c, state(), today=TODAY)
        day_one = blocks_on(result, TODAY)
        essay_minutes = sum(b.duration for b in day_one if b.kind == "essay")
        self.assertGreater(essay_minutes, 0, "essays were completely crowded out on day one")

    def test_creative_time_gets_its_weekly_floor(self):
        c = cfg()
        tasks = essays(10, dt.date(2026, 10, 1), c)
        result = planner.plan(tasks, c, state(), today=TODAY)
        weeks = {}
        for b in result.blocks:
            if b.type == "build":
                d = dt.date.fromisoformat(b.date)
                weeks.setdefault(f"{d.isocalendar().year}-{d.isocalendar().week}", 0)
                weeks[f"{d.isocalendar().year}-{d.isocalendar().week}"] += 1
        self.assertGreaterEqual(len(weeks), 2, "build-with-Claude time never got scheduled")
        self.assertGreaterEqual(max(weeks.values()), c["creative"]["min_sessions_per_week"])


class TestPhysicalValidity(unittest.TestCase):
    def test_no_two_blocks_overlap(self):
        c = cfg()
        tasks = essays(12, dt.date(2026, 10, 5), c)
        result = planner.plan(tasks, c, state(), today=TODAY)
        by_day = {}
        for b in result.blocks:
            by_day.setdefault(b.date, []).append(b)
        for day, blocks in by_day.items():
            blocks.sort(key=lambda b: b.start_minutes)
            for a, nxt in zip(blocks, blocks[1:]):
                self.assertLessEqual(a.end_minutes, nxt.start_minutes,
                                     f"overlap on {day}: {a.title} / {nxt.title}")

    def test_no_block_lands_on_dinner_the_call_or_school(self):
        c = cfg()
        tasks = essays(12, dt.date(2026, 10, 5), c)
        result = planner.plan(tasks, c, state(), today=TODAY)
        for b in result.blocks:
            d = dt.date.fromisoformat(b.date)
            for occ in daymodel.occurrences_for(d, c, state()):
                self.assertFalse(
                    b.start_minutes < occ.busy.end and b.end_minutes > occ.busy.start,
                    f"{b.title} on {b.date} collides with {occ.anchor.name}",
                )

    def test_nothing_is_scheduled_after_the_sleep_cutoff(self):
        c = cfg()
        tasks = essays(12, dt.date(2026, 10, 5), c)
        result = planner.plan(tasks, c, state(), today=TODAY)
        for b in result.blocks:
            d = dt.date.fromisoformat(b.date)
            bounds = daymodel.day_bounds(d, c, state())
            self.assertLessEqual(b.end_minutes, bounds.end, f"{b.title} runs past bedtime")
            self.assertGreaterEqual(b.start_minutes, bounds.start)

    def test_today_is_not_planned_in_the_past(self):
        c = cfg()
        tasks = essays(6, dt.date(2026, 10, 5), c)
        now = 19 * 60  # 7pm
        result = planner.plan(tasks, c, state(), today=TODAY, now_minutes=now)
        for b in blocks_on(result, TODAY):
            self.assertGreaterEqual(b.start_minutes, now)


class TestOvercommitment(unittest.TestCase):
    def test_impossible_workload_is_reported_not_silently_dropped(self):
        """40 hours of essays due in three days must surface, with options."""
        c = cfg()
        tasks = []
        for i in range(20):
            tasks.append(make_task(f"Crunch essay {i}", tasks, c, kind="essay",
                                   due=(TODAY + dt.timedelta(days=3)).isoformat(),
                                   estimate=120))
        result = planner.plan(tasks, c, state(), today=TODAY)
        surfaced = result.unplaceable + result.late
        self.assertTrue(surfaced, "overcommitment was silently swallowed")
        self.assertTrue(result.warnings, "an impossible load must warn, not look fine")
        for item in surfaced:
            self.assertTrue(item.options, "every surfaced item needs remediation options")

    def test_work_scheduled_past_its_deadline_is_flagged_as_late(self):
        """Placing a block is not success if it lands after the due date."""
        c = cfg()
        tasks = []
        for i in range(20):
            tasks.append(make_task(f"Crunch essay {i}", tasks, c, kind="essay",
                                   due=(TODAY + dt.timedelta(days=3)).isoformat(),
                                   estimate=120))
        result = planner.plan(tasks, c, state(), today=TODAY)
        self.assertTrue(result.late, "late work must be reported explicitly")
        for item in result.late:
            self.assertGreater(item.days_late, 0)
            self.assertGreater(
                dt.date.fromisoformat(item.scheduled),
                dt.date.fromisoformat(item.latest_ok),
            )

    def test_a_feasible_load_leaves_nothing_unplaceable(self):
        c = cfg()
        tasks = essays(6, dt.date(2026, 10, 20), c)
        result = planner.plan(tasks, c, state(), today=TODAY)
        self.assertEqual(result.unplaceable, [])
        self.assertEqual(result.late, [], "a comfortable load should have nothing late")


class TestReplanStability(unittest.TestCase):
    def test_finished_work_is_never_rescheduled(self):
        c = cfg()
        tasks = essays(5, dt.date(2026, 9, 30), c)
        tasks[0].stages[0].status = "done"
        tasks[0].stages[0].done_date = TODAY.isoformat()
        result = planner.plan(tasks, c, state(), today=TODAY)
        for b in result.blocks:
            self.assertFalse(
                b.task_id == tasks[0].id and b.stage_name == "draft",
                "a completed draft was scheduled again",
            )

    def test_planning_is_deterministic(self):
        c = cfg()
        tasks = essays(8, dt.date(2026, 10, 1), c)
        a = planner.plan(tasks, c, state(), today=TODAY)
        b = planner.plan(tasks, c, state(), today=TODAY)
        self.assertEqual(
            [(x.date, x.start, x.title) for x in a.blocks],
            [(x.date, x.start, x.title) for x in b.blocks],
        )


class TestSplitting(unittest.TestCase):
    def test_long_stages_split_into_sessions_within_the_cap(self):
        c = cfg()
        task = make_task("Huge essay", [], c, kind="essay", due="2026-10-15", estimate=600)
        result = planner.plan([task], c, state(), today=TODAY)
        for b in result.blocks:
            self.assertLessEqual(b.duration, c["max_block_minutes"])

    def test_split_never_produces_a_sliver(self):
        self.assertEqual(planner.split_minutes(100, 90, 25), [50, 50])
        self.assertEqual(planner.split_minutes(60, 90, 25), [60])
        for total in range(25, 400, 7):
            for size in planner.split_minutes(total, 90, 25):
                self.assertGreaterEqual(size, 25)
            self.assertEqual(sum(planner.split_minutes(total, 90, 25)), total)


if __name__ == "__main__":
    unittest.main()


class TestPlacementQuality(unittest.TestCase):
    def test_work_lands_in_good_hours_not_at_dawn(self):
        """A long free Saturday must not put essays at 7am and leave 3pm empty."""
        c = cfg()
        tasks = essays(20, dt.date(2026, 10, 20), c, spacing=1)
        result = planner.plan(tasks, c, state(), today=TODAY)
        sat = blocks_on(result, dt.date(2026, 9, 5))
        work = [b for b in sat if b.type == "work"]
        self.assertTrue(work, "expected work on the Saturday")
        for b in work:
            self.assertGreaterEqual(
                b.start_minutes, 11 * 60,
                f"'{b.title}' scheduled at {b.start} - too early to be realistic",
            )

    def test_weekday_work_starts_after_the_drive_home(self):
        c = cfg()
        tasks = essays(20, dt.date(2026, 10, 20), c, spacing=1)
        result = planner.plan(tasks, c, state(), today=TODAY)
        mon = [b for b in blocks_on(result, TODAY) if b.type == "work"]
        self.assertTrue(mon)
        self.assertGreaterEqual(mon[0].start_minutes, minutes_of(parse_time("15:12")))


class TestCadenceCountsCompletedWork(unittest.TestCase):
    def test_a_draft_finished_today_uses_up_todays_draft_slot(self):
        """Logging today's draft must not free the planner to schedule another."""
        c = cfg()
        tasks = essays(6, dt.date(2026, 10, 10), c)
        tasks[0].stages[0].status = "done"
        tasks[0].stages[0].done_date = TODAY.isoformat()
        result = planner.plan(tasks, c, state(), today=TODAY)
        drafts_today = [b for b in blocks_on(result, TODAY) if b.stage_name == "draft"]
        self.assertEqual(drafts_today, [], "cadence already spent by the completed draft")

    def test_a_draft_finished_yesterday_does_not_block_today(self):
        c = cfg()
        tasks = essays(6, dt.date(2026, 10, 10), c)
        tasks[0].stages[0].status = "done"
        tasks[0].stages[0].done_date = (TODAY - dt.timedelta(days=1)).isoformat()
        result = planner.plan(tasks, c, state(), today=TODAY)
        drafts_today = [b for b in blocks_on(result, TODAY) if b.stage_name == "draft"]
        self.assertEqual(len(drafts_today), 1)


class TestCoachCapacity(unittest.TestCase):
    """His coach reviews 1-2 pieces a day. That, not free time, is the limit."""

    def test_total_essay_blocks_per_day_respect_the_coach_cap(self):
        c = cfg()
        c["cadence"] = {}                     # let the combined cap do the work
        c["coach_capacity_per_day"] = 2
        tasks = essays(25, dt.date(2026, 11, 1), c)
        result = planner.plan(tasks, c, state(), today=TODAY)
        per_day = {}
        for b in result.blocks:
            if b.kind == "essay":
                per_day[b.date] = per_day.get(b.date, 0) + 1
        for day, n in per_day.items():
            self.assertLessEqual(n, 2, f"{n} essay blocks on {day}, coach caps at 2")

    def test_raising_the_cap_finishes_the_essays_sooner(self):
        """With room in the horizon the cap governs pace, not total volume."""
        c = cfg()
        c["cadence"] = {}
        c["coach_capacity_per_day"] = 2
        tasks = essays(25, dt.date(2026, 11, 1), c)

        def last_essay_day(result):
            return max(b.date for b in result.blocks if b.kind == "essay")

        slow = last_essay_day(planner.plan(tasks, c, state(), today=TODAY))
        c["coach_capacity_per_day"] = 4
        fast = last_essay_day(planner.plan(tasks, c, state(), today=TODAY))
        self.assertLess(fast, slow, "a bigger coach cap should finish earlier")

    def test_schoolwork_still_gets_placed_under_a_small_coach_cap(self):
        """Regression: the coach cap once shadowed the day's free-minute budget,
        zeroing the schoolwork allowance so homework was never scheduled."""
        c = cfg()
        c["coach_capacity_per_day"] = 2
        tasks = essays(5, dt.date(2026, 10, 15), c)
        hw = make_task("AP Gov reading", tasks, c, kind="schoolwork",
                       due=(TODAY + dt.timedelta(days=1)).isoformat(), estimate=45)
        tasks.append(hw)
        result = planner.plan(tasks, c, state(), today=TODAY)
        placed = [b for b in result.blocks if b.task_id == hw.id]
        self.assertTrue(placed, "schoolwork vanished under a small coach cap")
        self.assertEqual(placed[0].date, TODAY.isoformat())


class TestOverridesAndBlackouts(unittest.TestCase):
    def test_override_applies_only_inside_its_date_range(self):
        c = cfg()
        c["cadence"] = {}
        c["coach_capacity_per_day"] = 1
        c["cadence_overrides"] = [
            {"from": "2026-09-01", "to": "2026-09-10", "coach_capacity_per_day": 4}
        ]
        tasks = essays(30, dt.date(2026, 11, 1), c)
        result = planner.plan(tasks, c, state(), today=TODAY)
        per_day = {}
        for b in result.blocks:
            if b.kind == "essay":
                per_day[b.date] = per_day.get(b.date, 0) + 1
        inside = [n for d, n in per_day.items() if "2026-09-01" <= d <= "2026-09-10"]
        outside = [n for d, n in per_day.items() if d > "2026-09-10"]
        self.assertTrue(inside and max(inside) > 1, "banking window never exceeded the base cap")
        for n in inside:
            self.assertLessEqual(n, 4)
        for n in outside:
            self.assertLessEqual(n, 1, "base cap should apply after the override ends")

    def test_blackout_excludes_every_essay_stage_including_final(self):
        """A zeroed cadence is not enough - `final` has no cadence key and
        would otherwise leak straight into the test window."""
        c = cfg()
        c["blackouts"] = [
            {"from": "2026-09-11", "to": "2026-09-15", "tiers": [2], "reason": "test wall"}
        ]
        tasks = essays(25, dt.date(2026, 11, 1), c)
        result = planner.plan(tasks, c, state(), today=TODAY)
        for b in result.blocks:
            if "2026-09-11" <= b.date <= "2026-09-15":
                self.assertNotEqual(b.kind, "essay",
                                    f"'{b.title}' scheduled during the blackout on {b.date}")

    def test_an_essay_blackout_leaves_schoolwork_completely_untouched(self):
        """Blocking essays must not cost him a single hour of schoolwork."""
        c = cfg()
        tasks = essays(10, dt.date(2026, 11, 1), c)
        for i in range(6):
            tasks.append(make_task(f"Physics review {i}", tasks, c, kind="schoolwork",
                                   due="2026-09-18", estimate=60))
        before = planner.plan(tasks, c, state(), today=TODAY)
        c["blackouts"] = [{"from": "2026-09-11", "to": "2026-09-15", "tiers": [2]}]
        after = planner.plan(tasks, c, state(), today=TODAY)

        def schoolwork(result):
            return sorted((b.date, b.title) for b in result.blocks if b.kind == "schoolwork")

        self.assertEqual(schoolwork(before), schoolwork(after))
        self.assertTrue(schoolwork(after), "expected schoolwork to be scheduled at all")

    def test_blackout_targets_only_the_listed_tiers(self):
        c = cfg()
        c["blackouts"] = [{"from": "2026-09-11", "to": "2026-09-15", "tiers": [2]}]
        self.assertEqual(planner.blocked_tiers(dt.date(2026, 9, 12), c), {2})
        self.assertEqual(planner.blocked_tiers(dt.date(2026, 9, 16), c), set())
        self.assertEqual(planner.blocked_tiers(dt.date(2026, 9, 10), c), set())

    def test_blackout_does_not_silently_swallow_a_missed_deadline(self):
        """Work pushed past its due date by a blackout must still be reported."""
        c = cfg()
        c["blackouts"] = [{"from": "2026-09-01", "to": "2026-10-30", "tiers": [2]}]
        tasks = essays(3, dt.date(2026, 9, 20), c)
        result = planner.plan(tasks, c, state(), today=TODAY)
        self.assertTrue(result.late or result.unplaceable,
                        "a blackout must never become a silent hole")


class TestQuizEstimates(unittest.TestCase):
    def test_a_short_quiz_does_not_inflate(self):
        """min_block_minutes floored every review stage, turning 60 into 100."""
        c = cfg()
        for estimate in (30, 45, 60, 90):
            stages = stages_for("test", estimate, c)
            total = sum(s.minutes for s in stages)
            self.assertLessEqual(
                total, estimate + c["min_block_minutes"],
                f"{estimate}m quiz booked {total}m",
            )

    def test_a_full_test_still_gets_spaced_review(self):
        c = cfg()
        stages = stages_for("test", 240, c)
        self.assertGreaterEqual(len(stages), 3, "a real test should still spread out")
        self.assertEqual(sum(s.minutes for s in stages), 240)


class TestReviewTiming(unittest.TestCase):
    """Spaced review is only useful anchored to the test date."""

    def test_cram_lands_next_to_the_test_not_two_weeks_early(self):
        c = cfg()
        due = dt.date(2026, 9, 25)
        task = make_task("AP Bio unit test", [], c, kind="test",
                         due=due.isoformat(), estimate=240)
        result = planner.plan([task], c, state(), today=TODAY)
        cram = next(b for b in result.blocks if b.stage_name == "cram")
        days_before = (due - dt.date.fromisoformat(cram.date)).days
        self.assertLessEqual(days_before, 2, f"cram scheduled {days_before}d before the test")
        self.assertGreaterEqual(days_before, 0, "cram must not land after the test")

    def test_review_ladder_stays_in_order_and_spreads_out(self):
        c = cfg()
        task = make_task("AP Bio unit test", [], c, kind="test",
                         due="2026-09-25", estimate=240)
        result = planner.plan([task], c, state(), today=TODAY)
        mine = sorted((b for b in result.blocks if b.task_id == task.id),
                      key=lambda b: (b.date, b.start))
        self.assertEqual([b.stage_name for b in mine],
                         ["review-1", "review-2", "review-3", "cram"])
        self.assertGreaterEqual(len({b.date for b in mine}), 3,
                                "review should span several days")

    def test_review_does_not_begin_absurdly_early(self):
        c = cfg()
        due = dt.date(2026, 10, 30)          # two months out
        task = make_task("Far away test", [], c, kind="test",
                         due=due.isoformat(), estimate=240)
        result = planner.plan([task], c, state(), today=TODAY)
        first = min(b.date for b in result.blocks if b.task_id == task.id)
        lead = (due - dt.date.fromisoformat(first)).days
        self.assertLessEqual(lead, 14, f"review started {lead}d before the test")

    def test_cram_the_night_before_is_not_reported_as_late(self):
        """buffer_days must not turn correct test prep into a false alarm."""
        c = cfg()
        c["buffer_days"] = 2
        task = make_task("AP Bio unit test", [], c, kind="test",
                         due="2026-09-25", estimate=240)
        result = planner.plan([task], c, state(), today=TODAY)
        crams = [l for l in result.late if l.stage == "cram"]
        self.assertEqual(crams, [], "a cram the night before is on time, not late")

    def test_ordinary_work_still_keeps_its_buffer(self):
        """Only deadline-pinned stages opt out of buffer_days."""
        c = cfg()
        c["buffer_days"] = 2
        task = make_task("Lab report", [], c, kind="schoolwork",
                         due="2026-09-25", estimate=60)
        parts = planner.build_parts([task], c, {})
        part = parts[task.id][0]
        self.assertEqual(part.latest_date, dt.date(2026, 9, 23))


class TestWorkCannotStartBeforeItExists(unittest.TestCase):
    """Homework assigned next week must not be scheduled today."""

    def test_a_task_is_never_scheduled_before_available_from(self):
        c = cfg()
        task = make_task("Read Mod 2.8B", [], c, kind="schoolwork",
                         due="2026-09-17", available_from="2026-09-16", estimate=20)
        result = planner.plan([task], c, state(), today=TODAY)
        mine = [b for b in result.blocks if b.task_id == task.id]
        self.assertTrue(mine)
        for b in mine:
            self.assertGreaterEqual(b.date, "2026-09-16",
                                    f"scheduled {b.date}, assigned 2026-09-16")

    def test_review_never_precedes_the_material_being_taught(self):
        c = cfg()
        task = make_task("Unit 3 test", [], c, kind="test",
                         due="2026-09-25", available_from="2026-09-14", estimate=240)
        result = planner.plan([task], c, state(), today=TODAY)
        for b in result.blocks:
            if b.task_id == task.id:
                self.assertGreaterEqual(b.date, "2026-09-14")

    def test_available_from_composes_with_the_coach_gate(self):
        """Whichever gate is later wins; neither cancels the other."""
        c = cfg()
        task = make_task("Late-start essay", [], c, kind="essay",
                         due="2026-11-01", available_from="2026-09-20", estimate=120)
        task.stages[0].status = "done"
        task.stages[0].done_date = "2026-09-20"
        result = planner.plan([task], c, state(), today=TODAY)
        revise = next(b for b in result.blocks if b.stage_name == "revise-1")
        # coach gate: draft done 09-20 + 1 day latency
        self.assertGreaterEqual(revise.date, "2026-09-21")

    def test_no_buffer_false_alarm_when_work_lands_on_its_due_date(self):
        """Assigned and due the same day cannot honour buffer_days - not 'late'."""
        c = cfg()
        c["buffer_days"] = 2
        task = make_task("Email the supervisor", [], c, kind="admin",
                         due="2026-09-09", available_from="2026-09-09", estimate=15)
        result = planner.plan([task], c, state(), today=TODAY)
        self.assertEqual([l for l in result.late if l.task_id == task.id], [])

    def test_tasks_without_available_from_are_unaffected(self):
        c = cfg()
        tasks = essays(3, dt.date(2026, 10, 20), c)
        result = planner.plan(tasks, c, state(), today=TODAY)
        self.assertTrue(result.blocks)
        self.assertEqual(result.unplaceable, [])
