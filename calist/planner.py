"""The scheduler.

Three things make this different from a generic todo planner:

1. Work is placed only into windows that survive `daymodel` - travel and
   settle buffers are already gone before scheduling starts.
2. The essay coach is modelled as external latency. After a draft is written,
   the revise stage is UNSCHEDULABLE until the coach turnaround has elapsed.
3. A daily cadence (one draft + one revision) is satisfied before leftover
   capacity is filled, because that rhythm is what actually clears 20 essays.

Overcommitment is always reported, never silently dropped.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from . import daymodel
from .daymodel import Window
from .models import (
    Block,
    Task,
    fmt_time,
    time_from_minutes,
    weekday_key,
)


@dataclass
class Part:
    """One placeable piece of a stage. Long stages split across sessions."""

    task_id: str
    task_title: str
    kind: str
    tier: int
    school: str
    due: str | None
    stage_index: int
    stage_name: str
    part_index: int
    part_count: int
    minutes: int
    latest_date: dt.date | None
    is_final_part: bool
    earliest_date: dt.date | None = None

    @property
    def id(self) -> str:
        return f"{self.task_id}#{self.stage_index}.{self.part_index}"

    @property
    def label(self) -> str:
        base = f"{self.stage_name.capitalize()}: {self.task_title}"
        if self.part_count > 1:
            base += f" ({self.part_index + 1}/{self.part_count})"
        return base


@dataclass
class Unplaceable:
    part_id: str
    task_id: str
    title: str
    stage: str
    minutes: int
    due: str | None
    reason: str
    options: list[str] = field(default_factory=list)


@dataclass
class Late:
    """Scheduled, but after the point where it still meets the deadline.

    Distinct from Unplaceable: the work IS on the calendar, it is simply going
    to be late. Silently scheduling past a due date is the exact failure this
    planner exists to prevent, so it gets its own report.
    """

    part_id: str
    task_id: str
    title: str
    stage: str
    scheduled: str
    latest_ok: str
    days_late: int
    due: str | None
    options: list[str] = field(default_factory=list)


@dataclass
class PlanResult:
    generated: str
    start_date: str
    end_date: str
    blocks: list[Block]
    unplaceable: list[Unplaceable]
    late: list[Late]
    warnings: list[str]
    stats: dict[str, Any]


def _in_range(day: dt.date, spec: dict[str, Any]) -> bool:
    start = spec.get("from")
    end = spec.get("to")
    if start and day < dt.date.fromisoformat(start):
        return False
    if end and day > dt.date.fromisoformat(end):
        return False
    return True


def day_limits(day: dt.date, cfg: dict[str, Any]) -> tuple[dict[str, int], int]:
    """Resolve this day's stage cadence and combined coach capacity.

    The coach reviews only so much per day, so `coach_capacity_per_day` caps
    ALL essay stages together - that throughput, not free time, is what decides
    whether 35 essays get finished. Overrides let the cap change for a stretch
    (e.g. banking essays ahead of a test week).
    """
    cadence = dict(cfg.get("cadence", {}))
    capacity = int(cfg.get("coach_capacity_per_day", 0) or 0)
    for spec in cfg.get("cadence_overrides", []):
        if _in_range(day, spec):
            cadence.update(spec.get("cadence", {}))
            if spec.get("coach_capacity_per_day") is not None:
                capacity = int(spec["coach_capacity_per_day"])
    return cadence, capacity


def blocked_tiers(day: dt.date, cfg: dict[str, Any]) -> set[int]:
    """Tiers that must not be scheduled at all on this day.

    A zeroed cadence is not enough: stages with no cadence entry (`final`)
    would leak straight through into a test week.
    """
    blocked: set[int] = set()
    for spec in cfg.get("blackouts", []):
        if _in_range(day, spec):
            blocked.update(int(t) for t in spec.get("tiers", []))
    return blocked


def split_minutes(total: int, cap: int, floor: int) -> list[int]:
    """Split a stage into sessions no longer than `cap`, avoiding slivers."""
    total = max(0, int(total))
    if total == 0:
        return []
    if total <= cap:
        return [total]
    parts = -(-total // cap)  # ceil
    base = total // parts
    rem = total - base * parts
    sizes = [base + (1 if i < rem else 0) for i in range(parts)]
    # Fold a too-small tail into its neighbour rather than scheduling a sliver.
    if len(sizes) > 1 and sizes[-1] < floor:
        tail = sizes.pop()
        sizes[-1] += tail
    return sizes


def stage_lead_days(task: Task, stage_index: int) -> int:
    """Calendar days needed between finishing this stage and finishing the task.

    Includes the coach turnaround, so deadline pressure accounts for the wait.
    """
    days = 0
    for j in range(stage_index, len(task.stages) - 1):
        s = task.stages[j]
        days += max(1, s.awaits_days + s.gap_days_after + 1)
    return days


def build_parts(tasks: list[Task], cfg: dict[str, Any], multipliers: dict[str, float]) -> dict[str, list[Part]]:
    """Expand every unfinished stage into placeable parts, keyed by task id."""
    cap = int(cfg.get("max_block_minutes", 90))
    floor = int(cfg.get("min_block_minutes", 25))
    buffer_days = int(cfg.get("buffer_days", 2))

    by_task: dict[str, list[Part]] = {}
    for task in tasks:
        parts: list[Part] = []
        due = task.due_date
        for idx, stage in enumerate(task.stages):
            if stage.done:
                continue
            mult = multipliers.get(stage.name, 1.0)
            minutes = max(floor, int(round(stage.minutes * mult)))
            latest = None
            earliest = None
            if due:
                # buffer_days means "finish comfortably early". A stage pinned
                # near its deadline on purpose - a cram the night before a test -
                # opts out of it, otherwise correct scheduling reports as late.
                slack = 0 if stage.start_within_days is not None else buffer_days
                latest = due - dt.timedelta(days=slack + stage_lead_days(task, idx))
                if stage.start_within_days is not None:
                    earliest = due - dt.timedelta(days=int(stage.start_within_days))
            sizes = split_minutes(minutes, cap, floor)
            for p, size in enumerate(sizes):
                parts.append(
                    Part(
                        task_id=task.id,
                        task_title=task.title,
                        kind=task.kind,
                        tier=task.tier,
                        school=task.school,
                        due=task.due,
                        stage_index=idx,
                        stage_name=stage.name,
                        part_index=p,
                        part_count=len(sizes),
                        minutes=size,
                        latest_date=latest,
                        is_final_part=(p == len(sizes) - 1),
                        earliest_date=earliest,
                    )
                )
        if parts:
            by_task[task.id] = parts
    return by_task


def initial_available(task: Task, today: dt.date, cfg: dict[str, Any]) -> dt.date:
    """Earliest date the task's next unfinished stage may begin.

    If a draft was completed on the 3rd and the coach needs 2 days, the revise
    stage cannot be scheduled before the 5th. This is the coach gate.
    """
    latest_done_idx = -1
    latest_done_date: dt.date | None = None
    for i, s in enumerate(task.stages):
        if s.done:
            latest_done_idx = i
            if s.done_date:
                d = dt.date.fromisoformat(s.done_date[:10])
                if latest_done_date is None or d > latest_done_date:
                    latest_done_date = d
    if latest_done_idx < 0:
        return today
    stage = task.stages[latest_done_idx]
    wait = stage.awaits_days + stage.gap_days_after
    base = latest_done_date or today
    return max(today, base + dt.timedelta(days=wait))


def _sort_key(part: Part) -> tuple:
    far = dt.date(2099, 1, 1)
    return (part.latest_date or far, part.tier, part.task_title, part.stage_index, part.part_index)


DEFAULT_HOUR_WEIGHTS = {
    5: 0.15, 6: 0.25, 7: 0.30, 8: 0.30,   # before school - possible, but a bad bet
    12: 0.5, 13: 0.5, 14: 0.7,
    15: 1.00, 16: 1.00, 17: 0.95,          # home, settled, the real working hours
    18: 0.85, 19: 0.90, 20: 0.85,
    21: 0.65, 22: 0.45, 23: 0.25,          # tired, and near the cutoff
}


def hour_weight(hour: int, scores: dict[int, float] | None) -> float:
    if scores and hour in scores:
        return scores[hour]
    return DEFAULT_HOUR_WEIGHTS.get(hour, 0.5)


class _DayCanvas:
    """Free windows for one day, consumed as blocks are placed.

    Placement is preference-scored rather than earliest-fit. Squeezing an essay
    into 6:45am right after a 5:30am gym is technically valid and practically
    useless, so windows are ranked by how well the hours actually work for you -
    measured follow-through when habit data exists, sane defaults before that.
    """

    def __init__(self, windows: list[Window], gap: int, scores: dict[int, float] | None = None):
        self.windows = sorted(windows, key=lambda w: w.start)
        self.gap = gap
        self.scores = scores
        self.used = 0

    @property
    def remaining(self) -> int:
        return sum(w.minutes for w in self.windows)

    def _score(self, start: int, minutes: int) -> float:
        total, span = 0.0, 0
        for m in range(start, start + minutes, 15):
            total += hour_weight((m // 60) % 24, self.scores)
            span += 1
        return total / max(1, span)

    def _candidates(self, w: Window, minutes: int) -> list[int]:
        """Possible start positions inside a window.

        A long window (say 7am-6:30pm on a Saturday) spans hours that score very
        differently, so we consider positions THROUGHOUT it, not just its start.
        The window start is always a candidate so "right when you get home"
        stays available.
        """
        last = w.end - minutes
        if last < w.start:
            return []
        starts = {w.start, last}
        first_grid = ((w.start + 14) // 15) * 15
        starts.update(range(first_grid, last + 1, 15))
        return sorted(x for x in starts if w.start <= x <= last)

    def place(self, minutes: int) -> tuple[int, int] | None:
        """Take `minutes` from the best-scoring position across all windows."""
        best: tuple[float, int, int, int] | None = None  # (score, -start, index, start)
        for i, w in enumerate(self.windows):
            if w.minutes < minutes:
                continue
            for start in self._candidates(w, minutes):
                key = (self._score(start, minutes), -start, i, start)
                if best is None or key[:2] > best[:2]:
                    best = key
        if best is None:
            return None

        _, _, i, start = best
        w = self.windows[i]
        end = start + minutes
        head = Window(w.start, max(w.start, start - self.gap))
        tail = Window(min(end + self.gap, w.end), w.end)
        replacement = [x for x in (head, tail) if x.minutes > 0]
        self.windows[i:i + 1] = replacement
        self.windows.sort(key=lambda x: x.start)
        self.used += minutes
        return start, end


def plan(
    tasks: list[Task],
    cfg: dict[str, Any],
    state: dict[str, Any],
    today: dt.date | None = None,
    now_minutes: int | None = None,
    multipliers: dict[str, float] | None = None,
    hour_scores: dict[int, float] | None = None,
) -> PlanResult:
    today = today or dt.date.today()
    multipliers = multipliers or {}
    horizon = int(cfg.get("horizon_days", 45))
    gap = int(cfg.get("block_gap_minutes", 10))
    essay_floor = int(cfg.get("essay_floor_minutes_per_day", 60))
    creative = cfg.get("creative", {})

    by_id = {t.id: t for t in tasks}
    default_cadence = cfg.get("cadence", {})
    parts_by_task = build_parts([t for t in tasks if not t.done], cfg, multipliers)

    # Stages already finished on a given date count against that day's cadence.
    # Without this, drafting an essay this afternoon and logging it would still
    # leave the planner scheduling another draft the same day.
    already_done: dict[str, dict[str, int]] = {}
    for task in tasks:
        if task.tier != 2:
            continue
        for stage in task.stages:
            if stage.done and stage.done_date:
                day_key = stage.done_date[:10]
                counts = already_done.setdefault(day_key, {})
                counts[stage.name] = counts.get(stage.name, 0) + 1
                counts["_tier2"] = counts.get("_tier2", 0) + 1

    # Per-task cursor: which part comes next, and the earliest date it may run.
    cursor: dict[str, int] = {tid: 0 for tid in parts_by_task}
    available: dict[str, dt.date] = {
        tid: initial_available(by_id[tid], today, cfg) for tid in parts_by_task
    }

    # Reviewing for a test three weeks out is wasted effort and crowds out work
    # that is actually due. Hold each kind until it is within range of its date.
    windows_by_kind = cfg.get("start_within_days", {})
    for tid in parts_by_task:
        task = by_id[tid]
        within = windows_by_kind.get(task.kind)
        due = task.due_date
        if within and due:
            available[tid] = max(available[tid], min(due, due - dt.timedelta(days=int(within))))

    blocks: list[Block] = []
    warnings: list[str] = []
    placement: dict[str, tuple[Part, dt.date]] = {}
    creative_by_week: dict[str, int] = {}
    idle_minutes = 0
    idle_next_week = 0
    cadence_hits = {"draft": 0, "revise": 0}
    days_with_work = 0

    def ready_parts(day: dt.date) -> list[Part]:
        out = []
        for tid, plist in parts_by_task.items():
            i = cursor[tid]
            if i >= len(plist):
                continue
            if available[tid] > day:
                continue
            part = plist[i]
            if part.earliest_date and part.earliest_date > day:
                continue  # too far from the test for review to be worth anything
            out.append(part)
        out.sort(key=_sort_key)
        return out

    def capped(
        part: Part,
        day_counts: dict[str, int],
        cadence: dict[str, int],
        coach_cap: int,
        blocked: set[int],
    ) -> bool:
        """True if this part cannot be scheduled on this day.

        Three reasons, in order of how often they bite:
        the day is blacked out for this tier; the coach has already taken all
        she can review today; or this specific stage hit its own cadence limit.
        """
        if part.tier in blocked:
            return True
        if part.tier != 2:
            return False
        if coach_cap and day_counts.get("_tier2", 0) >= coach_cap:
            return True
        limit = cadence.get(part.stage_name)
        if limit is None:
            return False
        return day_counts.get(part.stage_name, 0) >= int(limit)

    def bump(day_counts: dict[str, int], part: Part) -> None:
        day_counts[part.stage_name] = day_counts.get(part.stage_name, 0) + 1
        if part.tier == 2:
            day_counts["_tier2"] = day_counts.get("_tier2", 0) + 1

    def commit(part: Part, day: dt.date, span: tuple[int, int]) -> None:
        start, end = span
        placement[part.id] = (part, day)
        blocks.append(
            Block(
                date=day.isoformat(),
                start=fmt_time(time_from_minutes(start)),
                end=fmt_time(time_from_minutes(end)),
                title=part.label,
                type="build" if part.kind == "build" else "work",
                chunk_id=part.id,
                task_id=part.task_id,
                stage_name=part.stage_name,
                kind=part.kind,
                note=part.school,
            )
        )
        cursor[part.task_id] += 1
        if part.is_final_part:
            # The coach gate: the next stage waits out the turnaround.
            stage = by_id[part.task_id].stages[part.stage_index]
            wait = max(0, stage.awaits_days + stage.gap_days_after)
            available[part.task_id] = day + dt.timedelta(days=wait)
        else:
            available[part.task_id] = day

    for offset in range(horizon):
        day = today + dt.timedelta(days=offset)
        clip = now_minutes if offset == 0 else None
        windows = daymodel.free_windows(day, cfg, state, clip)
        if not windows:
            continue
        canvas = _DayCanvas(windows, gap, hour_scores)
        capacity = canvas.remaining
        placed_today = 0
        day_counts: dict[str, int] = dict(already_done.get(day.isoformat(), {}))
        cadence, coach_cap = day_limits(day, cfg)
        blocked = blocked_tiers(day, cfg)
        week_key = f"{day.isocalendar().year}-W{day.isocalendar().week:02d}"

        # Reserve a slice for essays so schoolwork cannot crowd them out
        # for three straight weeks and blow the September target.
        reserve = min(essay_floor, capacity)
        tier1_budget = max(0, capacity - reserve)
        tier1_used = 0

        # --- pass 1: schoolwork and tests, most urgent first
        for part in ready_parts(day):
            if part.tier != 1 or part.tier in blocked:
                continue
            if tier1_used + part.minutes > tier1_budget:
                continue
            span = canvas.place(part.minutes)
            if span:
                commit(part, day, span)
                tier1_used += part.minutes
                placed_today += 1

        # --- pass 2: the essay cadence (one draft + one revision a day)
        for stage_name, target in cadence.items():
            done = day_counts.get(stage_name, 0)
            while done < int(target):
                candidate = next(
                    (
                        p
                        for p in ready_parts(day)
                        if p.tier == 2
                        and p.stage_name == stage_name
                        and not capped(p, day_counts, cadence, coach_cap, blocked)
                    ),
                    None,
                )
                if not candidate:
                    break
                span = canvas.place(candidate.minutes)
                if not span:
                    break
                commit(candidate, day, span)
                cadence_hits[stage_name] = cadence_hits.get(stage_name, 0) + 1
                bump(day_counts, candidate)
                done += 1
                placed_today += 1

        # --- pass 3: remaining essay work, but never past the cadence cap
        for part in ready_parts(day):
            if part.tier != 2 or capped(part, day_counts, cadence, coach_cap, blocked):
                continue
            span = canvas.place(part.minutes)
            if span:
                commit(part, day, span)
                cadence_hits[part.stage_name] = cadence_hits.get(part.stage_name, 0) + 1
                bump(day_counts, part)
                placed_today += 1

        # --- pass 4: building with Claude - a protected floor, earned
        want = int(creative.get("min_sessions_per_week", 0))
        got = creative_by_week.get(week_key, 0)
        if want and got < want:
            prefers = creative.get("preferred_days", [])
            days_left = 7 - day.weekday() if day.weekday() < 7 else 0
            must_take = (want - got) >= days_left  # running out of week
            eligible = (weekday_key(day) in prefers) or must_take
            day_complete = placed_today > 0 or not ready_parts(day)
            if 3 in blocked:
                eligible = False
            if eligible and (day_complete or not creative.get("requires_day_complete", True)):
                build_part = next(
                    (p for p in ready_parts(day) if p.tier == 3 and p.tier not in blocked), None
                )
                minutes = int(creative.get("session_minutes", 60))
                if build_part:
                    span = canvas.place(build_part.minutes)
                    if span:
                        commit(build_part, day, span)
                        creative_by_week[week_key] = got + 1
                        placed_today += 1
                else:
                    span = canvas.place(minutes)
                    if span:
                        blocks.append(
                            Block(
                                date=day.isoformat(),
                                start=fmt_time(time_from_minutes(span[0])),
                                end=fmt_time(time_from_minutes(span[1])),
                                title="Build something with Claude",
                                type="build",
                                kind="build",
                                note="earned - protected creative time",
                            )
                        )
                        creative_by_week[week_key] = got + 1
                        placed_today += 1

        # --- pass 5: anything else that fits (schoolwork overflow, spare stages)
        for part in ready_parts(day):
            if capped(part, day_counts, cadence, coach_cap, blocked):
                continue
            span = canvas.place(part.minutes)
            if span:
                commit(part, day, span)
                if part.tier == 2:
                    cadence_hits[part.stage_name] = cadence_hits.get(part.stage_name, 0) + 1
                bump(day_counts, part)
                placed_today += 1

        idle_minutes += canvas.remaining
        if offset < 7:
            idle_next_week += canvas.remaining
        if placed_today:
            days_with_work += 1

    # --- work that landed after its deadline is reported just as loudly
    late: list[Late] = []
    for part, day in placement.values():
        if part.latest_date and day > part.latest_date:
            late.append(
                Late(
                    part_id=part.id,
                    task_id=part.task_id,
                    title=part.task_title,
                    stage=part.stage_name,
                    scheduled=day.isoformat(),
                    latest_ok=part.latest_date.isoformat(),
                    days_late=(day - part.latest_date).days,
                    due=part.due,
                    options=[
                        "raise the daily cadence (more drafts/revisions per day)",
                        "raise daily capacity (later cutoff, fewer anchors)",
                        "trim the estimate, or drop this one",
                    ],
                )
            )
    late.sort(key=lambda l: (-l.days_late, l.scheduled))

    # --- whatever never fit at all is reported, loudly
    unplaceable: list[Unplaceable] = []
    for tid, plist in parts_by_task.items():
        for part in plist[cursor[tid]:]:
            task = by_id[tid]
            if part.latest_date and part.latest_date < today:
                reason = f"deadline already passed the point where this stage could start ({part.latest_date})"
                options = [
                    "drop or shorten this essay",
                    f"cut buffer_days (currently {cfg.get('buffer_days')})",
                    "ask the coach for a faster turnaround",
                ]
            elif part.latest_date:
                reason = f"no free time left before {part.latest_date}"
                options = [
                    "raise daily capacity (later sleep cutoff or fewer anchors)",
                    "trim the estimate",
                    "move the due date",
                ]
            else:
                reason = f"no free time inside the {horizon}-day horizon"
                options = ["extend horizon_days", "reduce workload"]
            unplaceable.append(
                Unplaceable(
                    part_id=part.id,
                    task_id=tid,
                    title=part.task_title,
                    stage=part.stage_name,
                    minutes=part.minutes,
                    due=part.due,
                    reason=reason,
                    options=options,
                )
            )

    # A revision drought means drafts are not going out fast enough.
    if cadence_hits.get("revise", 0) and cadence_hits.get("draft", 0):
        ratio = cadence_hits["revise"] / max(1, cadence_hits["draft"])
        if ratio < 0.6:
            warnings.append(
                "You are drafting much faster than you are revising - "
                "expect a pile-up when the coach returns feedback."
            )

    end = today + dt.timedelta(days=horizon - 1)
    stats = {
        "coach_capacity": cfg.get("coach_capacity_per_day"),
        "cadence_config": default_cadence,
        "tasks_total": len(tasks),
        "tasks_done": sum(1 for t in tasks if t.done),
        "blocks": len(blocks),
        "work_minutes": sum(b.duration for b in blocks),
        "days_with_work": days_with_work,
        "cadence": cadence_hits,
        "unplaceable": len(unplaceable),
        "late": len(late),
        "worst_days_late": max([l.days_late for l in late], default=0),
        "creative_sessions": sum(creative_by_week.values()),
        "idle_minutes": idle_minutes,
        "idle_hours_next_week": round(idle_next_week / 60, 1),
    }
    if late and idle_next_week > 240:
        warnings.append(
            f"You have {round(idle_next_week / 60, 1)}h unused this week but work is still "
            f"running late - the coach's daily capacity is the limit, not your time. "
            f"Raise coach_capacity_per_day in config.json to go faster."
        )

    if late:
        worst = late[0]
        warnings.append(
            f"{len(late)} item(s) are scheduled past their deadline - worst is "
            f"'{worst.title}' ({worst.stage}) {worst.days_late} day(s) late. "
            f"This workload does not fit the time you actually have."
        )

    blocks.sort(key=lambda b: (b.date, b.start))
    return PlanResult(
        generated=dt.datetime.now().isoformat(timespec="seconds"),
        start_date=today.isoformat(),
        end_date=end.isoformat(),
        blocks=blocks,
        unplaceable=unplaceable,
        late=late,
        warnings=warnings,
        stats=stats,
    )
