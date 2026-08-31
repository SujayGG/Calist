import datetime as dt
import unittest

from calist import daymodel, planner
from calist.models import minutes_of, parse_time
from calist.tasking import make_task
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
            if b.stage_name == "revise":
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
        revise = next(b for b in result.blocks if b.stage_name == "revise")
        gap = (dt.date.fromisoformat(revise.date) - TODAY).days
        self.assertGreaterEqual(gap, c["coach_latency_days"])


class TestCadence(unittest.TestCase):
    def test_at_most_one_draft_and_one_revision_per_day(self):
        """Spare capacity must not become five drafts the coach cannot absorb."""
        c = cfg()
        tasks = essays(20, dt.date(2026, 10, 15), c)
        result = planner.plan(tasks, c, state(), today=TODAY)
        per_day = {}
        for b in result.blocks:
            if b.stage_name in ("draft", "revise"):
                per_day.setdefault((b.date, b.stage_name), set()).add(b.task_id)
        for (day, stage), ids in per_day.items():
            self.assertLessEqual(len(ids), 1, f"{len(ids)} {stage} tasks on {day}")

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
