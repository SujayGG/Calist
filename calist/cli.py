"""Command line interface.

Designed so that Claude can drive it conversationally: every command is
non-interactive, takes flags, and prints a short human summary of what changed.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

from . import calibrate, clock, daymodel, habits, icsio, nlu, planner
from .models import Block, Task, fmt_clock, minutes_of, parse_time, to_jsonable
from .store import (
    DATA,
    ICS_PATH,
    PLAN_PATH,
    ensure_data_dir,
    load_config,
    load_state,
    load_tasks,
    log_event,
    read_json,
    save_config,
    save_state,
    save_tasks,
    validate_config,
    write_json,
)
from .tasking import DEFAULT_ESTIMATES, make_task, stages_for

BAR = "-" * 62


def _now_minutes() -> int:
    return clock.minutes_now(load_config())


def _today() -> dt.date:
    return clock.today(load_config())


def _load_plan() -> dict[str, Any]:
    return read_json(PLAN_PATH, {"blocks": [], "unplaceable": [], "late": [], "warnings": []})


def _blocks_from_plan(plan_data: dict[str, Any]) -> list[Block]:
    return [Block(**b) for b in plan_data.get("blocks", [])]


def _find_task(tasks: list[Task], needle: str) -> Task | None:
    needle = needle.strip()
    if "#" in needle:
        needle = needle.split("#", 1)[0]
    for t in tasks:
        if t.id == needle:
            return t
    matches = [t for t in tasks if needle.lower() in t.title.lower() or needle.lower() in t.id]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        print(f"'{needle}' matches {len(matches)} tasks:")
        for t in matches:
            print(f"  {t.id}  {t.title}")
        return None
    return None


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def cmd_setup(args: argparse.Namespace) -> int:
    ensure_data_dir()
    cfg = load_config()

    if args.school_end:
        for a in cfg["anchors"]:
            if a["id"] == "school":
                a["end"] = args.school_end
    if args.school_start:
        for a in cfg["anchors"]:
            if a["id"] == "school":
                a["start"] = args.school_start
    if args.drive is not None:
        for a in cfg["anchors"]:
            if a["id"] == "school":
                a["travel_after"] = args.drive
    if args.settle is not None:
        for a in cfg["anchors"]:
            if a["id"] == "school":
                a["settle_after"] = args.settle
    if args.target:
        cfg["target_date"] = args.target
    if args.coach_days is not None:
        cfg["coach_latency_days"] = args.coach_days
    if args.drafts_per_day is not None:
        cfg["cadence"]["draft"] = args.drafts_per_day
    if args.revisions_per_day is not None:
        cfg["cadence"]["revise"] = args.revisions_per_day
    if args.sleep_cutoff:
        cfg["sleep"]["cutoff_default"] = args.sleep_cutoff
    if args.gym_cutoff:
        cfg["sleep"]["cutoff_gym_day"] = args.gym_cutoff
    if args.wake:
        cfg["sleep"]["wake_default"] = args.wake
    if args.gym_wake:
        cfg["sleep"]["wake_gym_day"] = args.gym_wake
    if args.no_gym:
        cfg["anchors"] = [a for a in cfg["anchors"] if a.get("kind") != "gym"]

    save_config(cfg)
    print("Saved data/config.json")

    warnings = validate_config(cfg)
    if warnings:
        print("\nThings you should look at:")
        for w in warnings:
            print(f"  ! {w}")
    else:
        print("Config looks sane (sleep math included).")

    print()
    print(daymodel.describe_day(_today() + dt.timedelta(days=1), cfg, load_state()))
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    ensure_data_dir()
    cfg = load_config()
    tasks = load_tasks()
    task = make_task(
        args.title,
        tasks,
        cfg,
        kind=args.kind,
        due=args.due,
        estimate=args.estimate,
        school=args.school or "",
        notes=args.notes or "",
        source=args.source or "",
    )
    tasks.append(task)
    save_tasks(tasks)
    log_event("add", task_id=task.id, title=task.title, kind=task.kind, due=task.due)

    stages = " -> ".join(
        f"{s.name} {s.minutes}m" + (f" (+{s.awaits_days}d coach)" if s.awaits_days else "")
        for s in task.stages
    )
    print(f"Added [{task.id}] {task.title}")
    print(f"  kind={task.kind} due={task.due or 'none'} estimate={task.estimate_minutes}m")
    print(f"  stages: {stages}")
    if args.plan:
        return cmd_plan(argparse.Namespace(explain=False, quiet=False, ics=True))
    print("\nRun `calist plan` to fold this into the schedule.")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    tasks = load_tasks()
    if not tasks:
        print("No tasks yet. Add one with: calist add \"Purdue why-us essay\" --due 2026-09-20")
        return 0
    pending = [t for t in tasks if not t.done]
    finished = [t for t in tasks if t.done]
    print(f"{len(pending)} open, {len(finished)} done\n")
    for t in sorted(pending, key=lambda x: (x.due or "9999", x.title)):
        stages = "".join("#" if s.done else "." for s in t.stages)
        print(f"  [{t.id:<28}] {stages:<6} due {t.due or '-':<12} {t.title}")
    if finished and args.all:
        print("\nDone:")
        for t in finished:
            print(f"  [{t.id}] {t.title}")
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    ensure_data_dir()
    cfg = load_config()
    state = load_state()
    tasks = load_tasks()

    result = planner.plan(
        tasks,
        cfg,
        state,
        today=_today(),
        now_minutes=_now_minutes(),
        multipliers=calibrate.multipliers(),
        hour_scores=habits.follow_through_by_hour(),
    )
    write_json(PLAN_PATH, to_jsonable(result))
    state["last_plan"] = result.generated
    save_state(state)
    icsio.write_ics(result.blocks, ICS_PATH, name="Calist")

    if args.quiet:
        return 0

    s = result.stats
    print(BAR)
    print(f"PLAN  {result.start_date} -> {result.end_date}")
    print(BAR)
    print(f"  {s['blocks']} blocks, {round(s['work_minutes'] / 60, 1)}h of work across {s['days_with_work']} days")
    cad = s.get("cadence", {})
    drafts = cad.get("draft", 0)
    revisions = sum(n for stage, n in cad.items() if stage.startswith("revise"))
    finals = cad.get("final", 0)
    print(f"  essay rounds scheduled: {drafts} drafts, {revisions} revisions, {finals} finals")
    if s.get("coach_capacity"):
        print(f"  coach capacity: {s['coach_capacity']} pieces/day")
    print(f"  creative sessions: {s['creative_sessions']}")
    print(f"  unused capacity next 7 days: {s['idle_hours_next_week']}h")

    for w in result.warnings:
        print(f"\n  ! {w}")

    if result.late:
        print(f"\n  LATE ({len(result.late)}) - scheduled past the deadline:")
        for item in result.late[:8]:
            print(f"    {item.title} [{item.stage}] {item.days_late}d late (due {item.due})")
        if len(result.late) > 8:
            print(f"    ... and {len(result.late) - 8} more")
        print(f"    options: {'; '.join(result.late[0].options)}")

    if result.unplaceable:
        print(f"\n  NO ROOM AT ALL ({len(result.unplaceable)}):")
        for item in result.unplaceable[:8]:
            print(f"    {item.title} [{item.stage}] {item.minutes}m - {item.reason}")
        print(f"    options: {'; '.join(result.unplaceable[0].options)}")

    if not result.late and not result.unplaceable:
        print("\n  Everything fits with time to spare.")

    print(f"\nWrote data/plan.json and data/plan.ics")
    if args.explain:
        print()
        print(daymodel.describe_day(_today(), cfg, state))
    return 0


def _print_day(day: dt.date, blocks: list[Block], cfg: dict, state: dict, show_anchors: bool = True) -> None:
    print(BAR)
    print(f"{day.strftime('%A, %B %d')}")
    print(BAR)
    items: list[tuple[int, str]] = []
    if show_anchors:
        for occ in daymodel.occurrences_for(day, cfg, state):
            label = f"  {fmt_clock(parse_time(occ.anchor.start))}-{fmt_clock(parse_time(occ.anchor.end))}"
            items.append((occ.event.start, f"{label:<20} | {occ.anchor.name}"))
    for b in blocks:
        marker = "*" if b.type == "build" else " "
        label = f"  {fmt_clock(parse_time(b.start))}-{fmt_clock(parse_time(b.end))}"
        items.append((b.start_minutes, f"{label:<20} |{marker}{b.title}  ({b.duration}m)"))
    if not items:
        print("  nothing scheduled")
    for _, line in sorted(items, key=lambda x: x[0]):
        print(line)
    total = sum(b.duration for b in blocks)
    if total:
        print(f"\n  {round(total / 60, 1)}h of work in {len(blocks)} blocks")


def cmd_today(args: argparse.Namespace) -> int:
    cfg, state = load_config(), load_state()
    plan_data = _load_plan()
    day = _today() + dt.timedelta(days=args.offset)
    blocks = [b for b in _blocks_from_plan(plan_data) if b.date == day.isoformat()]
    _print_day(day, blocks, cfg, state)

    if args.days > 1:
        for i in range(1, args.days):
            nxt = day + dt.timedelta(days=i)
            print()
            _print_day(nxt, [b for b in _blocks_from_plan(plan_data)
                             if b.date == nxt.isoformat()], cfg, state)
    return 0


def cmd_next(args: argparse.Namespace) -> int:
    """What to do right now - also what the phone nudge asks for."""
    payload = current_focus()
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0
    print(payload["headline"])
    if payload.get("detail"):
        print(payload["detail"])
    return 0


def current_focus() -> dict[str, Any]:
    cfg, state = load_config(), load_state()
    plan_data = _load_plan()
    now = _now_minutes()
    today = _today().isoformat()
    blocks = [b for b in _blocks_from_plan(plan_data) if b.date == today]

    active = [b for b in blocks if b.start_minutes <= now < b.end_minutes]
    upcoming = [b for b in blocks if b.start_minutes > now]
    tasks = load_tasks()
    soonest = sorted(
        [t for t in tasks if not t.done and t.due],
        key=lambda t: t.due or "9999",
    )

    deadline = ""
    if soonest:
        due = soonest[0].due_date
        days = (due - _today()).days if due else None
        deadline = f"{soonest[0].title} due in {days}d" if days is not None else soonest[0].title

    if active:
        b = active[0]
        left = b.end_minutes - now
        return {
            "state": "in_block",
            "headline": f"NOW: {b.title}",
            "detail": f"{fmt_clock(parse_time(b.start))}-{fmt_clock(parse_time(b.end))} - {left} min left",
            "title": b.title,
            "minutes_left": left,
            "next_deadline": deadline,
        }
    if upcoming:
        b = min(upcoming, key=lambda x: x.start_minutes)
        wait = b.start_minutes - now
        return {
            "state": "before_block",
            "headline": f"NEXT: {b.title}",
            "detail": f"starts {fmt_clock(parse_time(b.start))} (in {wait} min)",
            "title": b.title,
            "minutes_until": wait,
            "next_deadline": deadline,
        }
    return {
        "state": "clear",
        "headline": "Nothing scheduled right now.",
        "detail": deadline,
        "title": "",
        "next_deadline": deadline,
    }


def cmd_done(args: argparse.Namespace) -> int:
    tasks = load_tasks()
    task = _find_task(tasks, args.task)
    if not task:
        print(f"No task matching '{args.task}'. Try `calist list`.")
        return 1

    if args.stage:
        idx = next((i for i, s in enumerate(task.stages) if s.name == args.stage and not s.done), None)
        if idx is None:
            print(f"No open stage called '{args.stage}' on {task.id}.")
            return 1
    else:
        idx = task.next_stage_index()
        if idx is None:
            print(f"{task.id} is already finished.")
            return 0

    stage = task.stages[idx]
    stage.status = "done"
    stage.done_date = (args.date or _today().isoformat())
    stage.actual_minutes = args.minutes
    save_tasks(tasks)

    log_event(
        "done",
        task_id=task.id,
        stage=stage.name,
        planned_minutes=stage.minutes,
        actual_minutes=args.minutes,
        scheduled_hour=args.hour if args.hour is not None else dt.datetime.now().hour,
    )

    print(f"Marked {task.title} [{stage.name}] done.")
    if idx + 1 < len(task.stages):
        nxt = task.stages[idx + 1]
        wait = stage.awaits_days + stage.gap_days_after
        when = dt.date.fromisoformat(stage.done_date) + dt.timedelta(days=wait)
        if stage.awaits_days:
            print(f"  Next: {nxt.name} - unlocked {when} once the coach returns it.")
        else:
            print(f"  Next: {nxt.name} - available {when}.")
    elif task.done:
        print(f"  {task.title} is COMPLETE.")

    if not args.no_plan:
        print()
        return cmd_plan(argparse.Namespace(explain=False, quiet=False, ics=True))
    return 0


def cmd_skip(args: argparse.Namespace) -> int:
    log_event("skip", task_id=args.task, reason=args.reason or "",
              scheduled_hour=args.hour if args.hour is not None else dt.datetime.now().hour)
    print(f"Logged a skip for {args.task}. It stays on the list and will be replanned.")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    cfg = load_config()
    tasks = load_tasks()
    plan_data = _load_plan()
    stats = plan_data.get("stats", {})

    essays = [t for t in tasks if t.kind == "essay"]
    done = [t for t in essays if t.done]
    drafted = [t for t in essays if t.stages and t.stages[0].done]

    print(BAR)
    print("STATUS")
    print(BAR)
    print(f"  essays: {len(done)}/{len(essays)} complete, {len(drafted)}/{len(essays)} drafted")
    target = cfg.get("target_date")
    if target:
        left = (dt.date.fromisoformat(target) - _today()).days
        print(f"  target {target} ({left} days away)")
    if stats:
        print(f"  planned: {stats.get('blocks', 0)} blocks, {stats.get('late', 0)} late, "
              f"{stats.get('unplaceable', 0)} with no room")
        print(f"  unused capacity next 7 days: {stats.get('idle_hours_next_week', 0)}h")

    by_school: dict[str, list[Task]] = {}
    for t in essays:
        by_school.setdefault(t.school or "unassigned", []).append(t)
    if by_school:
        print("\n  by school:")
        for school, items in sorted(by_school.items()):
            complete = sum(1 for t in items if t.done)
            bar = "#" * complete + "." * (len(items) - complete)
            print(f"    {school:<18} {bar:<12} {complete}/{len(items)}")

    cal = calibrate.explain()
    if cal:
        print("\n  how long things actually take you:")
        for line in cal:
            print(f"    {line}")

    h = habits.summary()
    if h["has_usage_data"]:
        print(f"\n  phone: {h['social_minutes_per_day']} min/day on social apps")
        if h["worst_hours"]:
            hours = ", ".join(f"{x['hour']}:00 ({int(x['minutes'])}m)" for x in h["worst_hours"])
            print(f"    worst hours: {hours}")
    if h["best_work_hours"]:
        hours = ", ".join(f"{x['hour']}:00 ({int(x['rate'] * 100)}%)" for x in h["best_work_hours"])
        print(f"    you follow through best at: {hours}")

    for w in plan_data.get("warnings", []):
        print(f"\n  ! {w}")
    return 0


def cmd_why(args: argparse.Namespace) -> int:
    cfg, state = load_config(), load_state()
    day = _today() + dt.timedelta(days=args.offset)
    print(daymodel.describe_day(day, cfg, state))
    for w in validate_config(cfg):
        print(f"\n  ! {w}")
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    path = Path(args.path)
    if not path.exists():
        print(f"No such file: {path}")
        return 1
    events = icsio.read_ics(path)
    print(f"Read {len(events)} events from {path.name}")

    if args.as_anchors:
        cfg = load_config()
        specs = icsio.events_to_anchor_specs(events)
        added = 0
        existing = {a.get("name") for a in cfg["anchors"]}
        for i, spec in enumerate(specs):
            if spec["name"] in existing:
                continue
            spec["id"] = f"imported-{len(cfg['anchors']) + i}"
            cfg["anchors"].append(spec)
            added += 1
        save_config(cfg)
        print(f"Added {added} anchors to config.json (review the travel buffers!).")
        return 0

    cfg = load_config()
    tasks = load_tasks()
    added = 0
    for ev in events:
        title = ev.get("summary", "").strip()
        if not title:
            continue
        due = ev["dtstart"].date().isoformat()
        tasks.append(make_task(title, tasks, cfg, kind=args.kind, due=due,
                               estimate=args.estimate, source=path.name))
        added += 1
    save_tasks(tasks)
    print(f"Added {added} tasks. Run `calist plan`.")
    return 0


def cmd_usage(args: argparse.Namespace) -> int:
    """Manual habit entry - the fallback when the phone macros aren't running."""
    from .store import USAGE_PATH, append_jsonl

    hours = [int(h) for h in args.hours.split(",")] if args.hours else None
    record = {
        "ts": dt.datetime.now().isoformat(timespec="seconds"),
        "date": args.date or _today().isoformat(),
        "app": args.app,
        "minutes": args.minutes,
        "source": "manual",
    }
    if hours:
        record["hours"] = hours
    append_jsonl(USAGE_PATH, record)
    print(f"Logged {args.minutes} min of {args.app}.")
    s = habits.summary()
    if s["risk_hours"]:
        print(f"  risk hours so far: {', '.join(f'{h}:00' for h in s['risk_hours'])}")
    return 0


def cmd_ics(args: argparse.Namespace) -> int:
    plan_data = _load_plan()
    blocks = _blocks_from_plan(plan_data)
    if not blocks:
        print("No plan yet - run `calist plan` first.")
        return 1
    path = icsio.write_ics(blocks, Path(args.out) if args.out else ICS_PATH)
    print(f"Wrote {len(blocks)} events to {path}")
    print("Subscribe to this file in Google Calendar (Other calendars -> From URL/import).")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from .server import serve

    cfg = load_config()
    host = args.host or cfg["server"]["host"]
    port = args.port or cfg["server"]["port"]
    serve(host, port)
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    from .watch import run_watcher

    return run_watcher(dry_run=args.dry_run, once=args.once)


def cmd_say(args: argparse.Namespace) -> int:
    """Change the schedule by describing the change."""
    cfg, tasks = load_config(), load_tasks()

    if args.check:
        result = nlu.check_model(cfg)
        print(f"  endpoint : {result['endpoint']}")
        print(f"  model    : {result['model']}")
        if result["installed_models"]:
            print(f"  installed: {', '.join(result['installed_models'])}")
        print(f"  status   : {'CONNECTED' if result['ok'] else 'NOT CONNECTED'}")
        print(f"  {result['detail']}")
        if not result["ok"]:
            print("\n  The rules parser still works without it:")
            print('     py -m calist say --no-model "done purdue essay 1, took 90 min"')
        return 0 if result["ok"] else 1

    text = " ".join(args.words).strip()
    if not text:
        print('Say something, e.g. calist say "done purdue essay 1, took 90 min"')
        return 1

    try:
        command = nlu.parse(text, cfg, tasks, use_model=not args.no_model)
    except nlu.ParseError as exc:
        print(f"  {exc}")
        return 1

    plan_view = nlu.describe(command, tasks, cfg, allow_all=args.all)
    print(f"  {plan_view['summary']}")
    if plan_view.get("error") == "ambiguous":
        for c in plan_view["choices"]:
            print(f"     - {c['id']:<30} {c['title']}")
        if plan_view.get("bulk_possible"):
            print("  Name one of these, or add --all to apply to all of them.")
        else:
            print("  Name one of these and try again.")
        return 1
    if plan_view.get("choices"):
        for c in plan_view["choices"]:
            print(f"     - {c['id']:<30} {c['title']}")
    if plan_view.get("error"):
        return 1
    if command.source == "model":
        print("  (interpreted by the local model)")

    if not args.yes:
        reply = input("  Apply? [y/N] ").strip().lower()
        if reply not in ("y", "yes"):
            print("  Left alone.")
            return 0

    try:
        result = nlu.apply(command)
    except nlu.ParseError as exc:
        print(f"  {exc}")
        return 1

    print(f"  {result.get('detail', 'done')}")
    if "late" in result:
        print(f"  replanned: {result['late']} late, {result['unplaceable']} with no room")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="calist", description="A realistic daily planner.")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("setup", help="configure your real week")
    s.add_argument("--school-start")
    s.add_argument("--school-end", help="e.g. 14:40")
    s.add_argument("--drive", type=int, help="minutes driving home")
    s.add_argument("--settle", type=int, help="minutes to decompress before work")
    s.add_argument("--target", help="date most essays should be done by")
    s.add_argument("--coach-days", type=int, help="essay coach turnaround in days")
    s.add_argument("--drafts-per-day", type=int)
    s.add_argument("--revisions-per-day", type=int)
    s.add_argument("--sleep-cutoff")
    s.add_argument("--gym-cutoff")
    s.add_argument("--wake")
    s.add_argument("--gym-wake")
    s.add_argument("--no-gym", action="store_true")
    s.set_defaults(func=cmd_setup)

    s = sub.add_parser("add", help="add a task")
    s.add_argument("title")
    s.add_argument("--kind", default="essay", choices=sorted(DEFAULT_ESTIMATES))
    s.add_argument("--due")
    s.add_argument("--estimate", type=int, help="total minutes")
    s.add_argument("--school")
    s.add_argument("--notes")
    s.add_argument("--source")
    s.add_argument("--plan", action="store_true", help="replan immediately")
    s.set_defaults(func=cmd_add)

    s = sub.add_parser("list", help="list tasks")
    s.add_argument("--all", action="store_true")
    s.set_defaults(func=cmd_list)

    s = sub.add_parser("plan", help="rebuild the schedule")
    s.add_argument("--explain", action="store_true")
    s.add_argument("--quiet", action="store_true")
    s.add_argument("--ics", action="store_true", default=True)
    s.set_defaults(func=cmd_plan)

    s = sub.add_parser("today", help="show the day")
    s.add_argument("--offset", type=int, default=0)
    s.add_argument("--days", type=int, default=1)
    s.set_defaults(func=cmd_today)

    s = sub.add_parser("next", help="what to do right now")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_next)

    s = sub.add_parser("done", help="mark a stage complete")
    s.add_argument("task")
    s.add_argument("--stage")
    s.add_argument("--minutes", type=int)
    s.add_argument("--hour", type=int)
    s.add_argument("--date")
    s.add_argument("--no-plan", action="store_true")
    s.set_defaults(func=cmd_done)

    s = sub.add_parser("skip", help="log a skipped block")
    s.add_argument("task")
    s.add_argument("--reason")
    s.add_argument("--hour", type=int)
    s.set_defaults(func=cmd_skip)

    s = sub.add_parser("status", help="progress and what the app has learned")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("why", help="explain where a day's time goes")
    s.add_argument("--offset", type=int, default=0)
    s.set_defaults(func=cmd_why)

    s = sub.add_parser("import", help="import an .ics file")
    s.add_argument("path")
    s.add_argument("--as-anchors", action="store_true", help="treat events as fixed commitments")
    s.add_argument("--kind", default="schoolwork", choices=sorted(DEFAULT_ESTIMATES))
    s.add_argument("--estimate", type=int)
    s.set_defaults(func=cmd_import)

    s = sub.add_parser("usage", help="log phone usage by hand")
    s.add_argument("app")
    s.add_argument("minutes", type=int)
    s.add_argument("--hours", help="comma separated, e.g. 20,21,22")
    s.add_argument("--date")
    s.set_defaults(func=cmd_usage)

    s = sub.add_parser("ics", help="export the calendar file")
    s.add_argument("--out")
    s.set_defaults(func=cmd_ics)

    s = sub.add_parser("serve", help="run the dashboard")
    s.add_argument("--host")
    s.add_argument("--port", type=int)
    s.set_defaults(func=cmd_serve)

    s = sub.add_parser("say", help="change the schedule in plain language")
    s.add_argument("words", nargs="*")
    s.add_argument("--yes", "-y", action="store_true", help="skip the confirmation")
    s.add_argument("--no-model", action="store_true", help="rules only, never call the model")
    s.add_argument("--all", action="store_true",
                   help="apply to every matching task, not just one")
    s.add_argument("--check", action="store_true",
                   help="test the connection to the local model and exit")
    s.set_defaults(func=cmd_say)

    s = sub.add_parser("watch", help="run the distraction nudge watcher")
    s.add_argument("--dry-run", action="store_true", help="print detections, never nudge")
    s.add_argument("--once", action="store_true")
    s.set_defaults(func=cmd_watch)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    ensure_data_dir()
    return args.func(args)
