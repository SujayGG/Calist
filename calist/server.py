"""Local dashboard + JSON API.

Stdlib http.server only. Two audiences:
  * the browser dashboard on your laptop
  * the phone, which hits /api/now for nudge text and posts app usage

Binding to 0.0.0.0 exposes this to your whole network with no login, so a
token can be set in config.json; the server warns loudly if you go wide open.
"""

from __future__ import annotations

import datetime as dt
import json
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from . import calibrate, daymodel, habits
from .models import Block, fmt_clock, parse_time, to_jsonable
from .store import (
    PLAN_PATH,
    ROOT,
    USAGE_PATH,
    append_jsonl,
    load_config,
    load_state,
    load_tasks,
    log_event,
    read_json,
    save_tasks,
)

STATIC = ROOT / "dashboard"
MAX_BODY = 2 * 1024 * 1024

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".ics": "text/calendar; charset=utf-8",
    ".json": "application/json; charset=utf-8",
}


def _plan() -> dict[str, Any]:
    return read_json(PLAN_PATH, {"blocks": [], "unplaceable": [], "late": [], "warnings": [], "stats": {}})


def day_payload(day: dt.date) -> dict[str, Any]:
    cfg, state = load_config(), load_state()
    blocks = [b for b in _plan().get("blocks", []) if b["date"] == day.isoformat()]
    anchors = [
        {
            "name": occ.anchor.name,
            "kind": occ.anchor.kind,
            "start": occ.anchor.start,
            "end": occ.anchor.end,
            "start_label": fmt_clock(parse_time(occ.anchor.start)),
            "end_label": fmt_clock(parse_time(occ.anchor.end)),
            "travel_after": occ.anchor.travel_after,
            "settle_after": occ.anchor.settle_after,
        }
        for occ in daymodel.occurrences_for(day, cfg, state)
    ]
    for b in blocks:
        b["start_label"] = fmt_clock(parse_time(b["start"]))
        b["end_label"] = fmt_clock(parse_time(b["end"]))
    windows = [
        {"start": w.start, "end": w.end, "minutes": w.minutes, "label": w.label()}
        for w in daymodel.free_windows(day, cfg, state)
    ]
    return {
        "date": day.isoformat(),
        "weekday": day.strftime("%A"),
        "pretty": day.strftime("%A, %B %d"),
        "blocks": blocks,
        "anchors": anchors,
        "free_windows": windows,
        "work_minutes": sum(
            (parse_time(b["end"]).hour * 60 + parse_time(b["end"]).minute)
            - (parse_time(b["start"]).hour * 60 + parse_time(b["start"]).minute)
            for b in blocks
        ),
    }


def status_payload() -> dict[str, Any]:
    cfg = load_config()
    tasks = load_tasks()
    plan = _plan()
    essays = [t for t in tasks if t.kind == "essay"]

    def stage_done(t, name):
        return any(s.name == name and s.done for s in t.stages)

    target = cfg.get("target_date")
    days_left = None
    if target:
        days_left = (dt.date.fromisoformat(target) - dt.date.today()).days

    by_school: dict[str, dict[str, int]] = {}
    for t in essays:
        key = t.school or "unassigned"
        entry = by_school.setdefault(key, {"total": 0, "done": 0, "drafted": 0})
        entry["total"] += 1
        entry["done"] += 1 if t.done else 0
        entry["drafted"] += 1 if stage_done(t, "draft") else 0

    return {
        "target_date": target,
        "days_to_target": days_left,
        "essays": {
            "total": len(essays),
            "drafted": sum(1 for t in essays if stage_done(t, "draft")),
            "revised": sum(1 for t in essays if stage_done(t, "revise")),
            "done": sum(1 for t in essays if t.done),
        },
        "by_school": by_school,
        "tasks": [
            {
                "id": t.id,
                "title": t.title,
                "kind": t.kind,
                "school": t.school,
                "due": t.due,
                "done": t.done,
                "stages": [
                    {"name": s.name, "done": s.done, "minutes": s.minutes,
                     "done_date": s.done_date, "awaits_days": s.awaits_days}
                    for s in t.stages
                ],
            }
            for t in sorted(tasks, key=lambda x: (x.done, x.due or "9999"))
        ],
        "stats": plan.get("stats", {}),
        "warnings": plan.get("warnings", []),
        "late": plan.get("late", []),
        "unplaceable": plan.get("unplaceable", []),
        "calibration": calibrate.explain(),
        "habits": habits.summary(),
        "streak": streak_days(),
    }


def streak_days() -> int:
    """Consecutive days back from today with at least one completion."""
    from .store import LOG_PATH, read_jsonl

    done_days = {
        str(r.get("ts", ""))[:10] for r in read_jsonl(LOG_PATH) if r.get("type") == "done"
    }
    streak, cursor = 0, dt.date.today()
    while cursor.isoformat() in done_days:
        streak += 1
        cursor -= dt.timedelta(days=1)
    return streak


def mark_done(task_id: str, stage_name: str | None, minutes: int | None, hour: int | None) -> dict[str, Any]:
    tasks = load_tasks()
    task = next((t for t in tasks if t.id == task_id), None)
    if not task:
        return {"ok": False, "error": f"no task {task_id}"}
    if stage_name:
        idx = next((i for i, s in enumerate(task.stages) if s.name == stage_name and not s.done), None)
    else:
        idx = task.next_stage_index()
    if idx is None:
        return {"ok": False, "error": "no open stage"}
    stage = task.stages[idx]
    stage.status = "done"
    stage.done_date = dt.date.today().isoformat()
    stage.actual_minutes = minutes
    save_tasks(tasks)
    log_event("done", task_id=task.id, stage=stage.name, planned_minutes=stage.minutes,
              actual_minutes=minutes, scheduled_hour=hour if hour is not None else dt.datetime.now().hour)
    return {"ok": True, "task": task.id, "stage": stage.name, "task_done": task.done}


def replan() -> dict[str, Any]:
    from . import icsio, planner
    from .store import ICS_PATH, write_json

    cfg, state, tasks = load_config(), load_state(), load_tasks()
    now = dt.datetime.now()
    result = planner.plan(
        tasks, cfg, state, today=dt.date.today(),
        now_minutes=now.hour * 60 + now.minute,
        multipliers=calibrate.multipliers(),
        hour_scores=habits.follow_through_by_hour(),
    )
    payload = to_jsonable(result)
    write_json(PLAN_PATH, payload)
    icsio.write_ics(result.blocks, ICS_PATH, name="Calist")
    return payload


class Handler(BaseHTTPRequestHandler):
    server_version = "Calist"
    token: str = ""

    def log_message(self, fmt, *args):  # quieter console
        return

    # -- helpers ---------------------------------------------------------
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, payload: Any, code: int = 200) -> None:
        self._send(code, json.dumps(payload, ensure_ascii=False).encode(), CONTENT_TYPES[".json"])

    def _text(self, text: str, code: int = 200) -> None:
        self._send(code, text.encode(), "text/plain; charset=utf-8")

    def _authorised(self, query: dict[str, list[str]]) -> bool:
        if not self.token:
            return True
        return (query.get("t") or [""])[0] == self.token

    def _read_body(self) -> Any:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            return None
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None

    def _serve_static(self, path: str) -> None:
        name = "index.html" if path in ("/", "") else path.lstrip("/")
        target = (STATIC / name).resolve()
        if not str(target).startswith(str(STATIC.resolve())) or not target.is_file():
            self._text("not found", 404)
            return
        ctype = CONTENT_TYPES.get(target.suffix, "application/octet-stream")
        self._send(200, target.read_bytes(), ctype)

    # -- routes ----------------------------------------------------------
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        route, query = parsed.path, parse_qs(parsed.query)

        if not route.startswith("/api/"):
            if route == "/plan.ics":
                from .store import ICS_PATH
                if ICS_PATH.exists():
                    self._send(200, ICS_PATH.read_bytes(), CONTENT_TYPES[".ics"])
                else:
                    self._text("no plan yet", 404)
                return
            self._serve_static(route)
            return

        if not self._authorised(query):
            self._json({"error": "bad or missing token"}, 403)
            return

        if route == "/api/now":
            from .cli import current_focus
            focus = current_focus()
            if (query.get("format") or [""])[0] == "text":
                line = focus["headline"]
                if focus.get("next_deadline"):
                    line += f" | {focus['next_deadline']}"
                self._text(line)
            else:
                self._json(focus)
            return

        if route == "/api/today":
            offset = int((query.get("offset") or ["0"])[0])
            self._json(day_payload(dt.date.today() + dt.timedelta(days=offset)))
            return

        if route == "/api/week":
            start = dt.date.today()
            self._json({"days": [day_payload(start + dt.timedelta(days=i)) for i in range(7)]})
            return

        if route == "/api/status":
            self._json(status_payload())
            return

        if route == "/api/plan":
            self._json(_plan())
            return

        self._json({"error": "unknown endpoint"}, 404)

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        route, query = parsed.path, parse_qs(parsed.query)
        if not self._authorised(query):
            self._json({"error": "bad or missing token"}, 403)
            return

        body = self._read_body()

        if route == "/api/done":
            if not isinstance(body, dict) or not body.get("task_id"):
                self._json({"ok": False, "error": "task_id required"}, 400)
                return
            result = mark_done(body["task_id"], body.get("stage"),
                               body.get("minutes"), body.get("hour"))
            if result.get("ok"):
                result["plan"] = replan().get("stats", {})
            self._json(result)
            return

        if route == "/api/skip":
            if not isinstance(body, dict):
                self._json({"ok": False, "error": "bad body"}, 400)
                return
            log_event("skip", task_id=body.get("task_id", ""), reason=body.get("reason", ""),
                      scheduled_hour=body.get("hour", dt.datetime.now().hour))
            self._json({"ok": True})
            return

        if route == "/api/usage":
            # The phone posts either one event or a batch of them.
            records = body if isinstance(body, list) else [body] if isinstance(body, dict) else []
            written = 0
            for rec in records:
                if not isinstance(rec, dict) or not rec.get("app"):
                    continue
                rec.setdefault("ts", dt.datetime.now().isoformat(timespec="seconds"))
                rec.setdefault("source", "phone")
                append_jsonl(USAGE_PATH, rec)
                written += 1
            self._json({"ok": True, "written": written})
            return

        if route == "/api/plan":
            self._json({"ok": True, "stats": replan().get("stats", {})})
            return

        self._json({"error": "unknown endpoint"}, 404)


def serve(host: str = "127.0.0.1", port: int = 8787) -> None:
    cfg = load_config()
    token = cfg.get("server", {}).get("token", "")
    Handler.token = token

    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"Calist dashboard: http://{host if host != '0.0.0.0' else '127.0.0.1'}:{port}")
    if host not in ("127.0.0.1", "localhost"):
        if token:
            print(f"  LAN access enabled, token required (?t={token[:4]}...)")
        else:
            print("  ! Listening on the network with NO token - anyone on this wifi can read")
            print("    and modify your schedule. Set server.token in data/config.json,")
            print(f"    e.g. \"token\": \"{secrets.token_urlsafe(12)}\"")
    print("  Ctrl-C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
