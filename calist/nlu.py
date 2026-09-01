"""Turning plain sentences into schedule changes.

Two rules shape this module.

**Rules first, model second.** Most of what gets typed is a grammar, not a
language problem - "done purdue 90 min", "add bio test sept 14 4 hours". Those
are parsed deterministically: instantly, offline, with no model running. The
model is only a fallback for phrasing the rules miss, so the command bar keeps
working when Ollama is not up.

**The model never writes to the store.** It returns a JSON command that is
validated against a strict schema here and then executed by ordinary code. An
invalid or unparseable response is reported, never guessed at.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from . import clock
from .models import Task
from .store import load_config, load_tasks, save_config, save_tasks, log_event
from .tasking import DEFAULT_ESTIMATES, make_task

ACTIONS = {"add", "done", "skip", "move", "blackout", "anchor", "replan"}

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7,
    "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}
WEEKDAYS = {"mon": 0, "tue": 1, "tues": 1, "wed": 2, "thu": 3, "thur": 3,
            "thurs": 3, "fri": 4, "sat": 5, "sun": 6}
KIND_WORDS = {
    "essay": "essay", "supplement": "essay", "piq": "essay",
    "test": "test", "exam": "test", "quiz": "test", "midterm": "test",
    "lab": "schoolwork", "homework": "schoolwork", "hw": "schoolwork",
    "reading": "schoolwork", "worksheet": "schoolwork", "problem set": "schoolwork",
    "build": "build", "project": "build",
    "email": "admin", "form": "admin",
}


class ParseError(Exception):
    """Raised when a sentence cannot be turned into a safe command."""


@dataclass
class Command:
    action: str
    params: dict[str, Any] = field(default_factory=dict)
    source: str = "rules"          # rules | model
    text: str = ""

    def to_json(self) -> dict[str, Any]:
        return {"action": self.action, "params": self.params, "source": self.source}


# ---------------------------------------------------------------- dates
def parse_date(text: str, today: dt.date) -> dt.date | None:
    """Understand the date forms a person actually types."""
    t = text.lower().strip()

    if re.search(r"\btoday\b|\btonight\b", t):
        return today
    if re.search(r"\btomorrow\b", t):
        return today + dt.timedelta(days=1)
    if re.search(r"\byesterday\b", t):
        return today - dt.timedelta(days=1)

    m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", t)
    if m:
        return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))

    # 9/20 or 9/20/26
    m = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", t)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        year = int(m.group(3) or today.year)
        if year < 100:
            year += 2000
        return _safe_date(year, month, day, today)

    # "sept 20", "september 20th"
    m = re.search(r"\b(" + "|".join(MONTHS) + r")[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b", t)
    if m:
        return _safe_date(today.year, MONTHS[m.group(1)], int(m.group(2)), today)

    # "20 sept"
    m = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(" + "|".join(MONTHS) + r")[a-z]*\b", t)
    if m:
        return _safe_date(today.year, MONTHS[m.group(2)], int(m.group(1)), today)

    # "next friday" / "friday"
    m = re.search(r"\b(next\s+)?(" + "|".join(WEEKDAYS) + r")[a-z]*\b", t)
    if m:
        target = WEEKDAYS[m.group(2)]
        ahead = (target - today.weekday()) % 7
        if ahead == 0:
            ahead = 7
        if m.group(1):
            ahead += 7 if ahead <= 7 else 0
        return today + dt.timedelta(days=ahead)

    if re.search(r"\bnext week\b", t):
        return today + dt.timedelta(days=(7 - today.weekday()))

    # "the 14th"
    m = re.search(r"\bthe\s+(\d{1,2})(?:st|nd|rd|th)\b", t)
    if m:
        return _safe_date(today.year, today.month, int(m.group(1)), today, roll=True)
    return None


def _safe_date(year: int, month: int, day: int, today: dt.date, roll: bool = False) -> dt.date | None:
    try:
        d = dt.date(year, month, day)
    except ValueError:
        return None
    # A bare day-of-month in the past almost always means next month.
    if roll and d < today:
        month += 1
        if month > 12:
            month, year = 1, year + 1
        try:
            d = dt.date(year, month, day)
        except ValueError:
            return None
    # A month/day already well past probably means next year.
    if not roll and (today - d).days > 300:
        try:
            d = dt.date(year + 1, month, day)
        except ValueError:
            return None
    return d


def parse_minutes(text: str) -> int | None:
    t = text.lower()
    m = re.search(r"\b(\d+(?:\.\d+)?)\s*(?:h|hr|hrs|hour|hours)\b", t)
    if m:
        return int(round(float(m.group(1)) * 60))
    m = re.search(r"\b(\d+)\s*(?:m|min|mins|minute|minutes)\b", t)
    if m:
        return int(m.group(1))
    m = re.search(r"\b(an hour and a half|hour and a half)\b", t)
    if m:
        return 90
    if re.search(r"\ban hour\b", t):
        return 60
    return None


def guess_kind(text: str) -> str | None:
    t = text.lower()
    for word, kind in KIND_WORDS.items():
        if re.search(r"\b" + re.escape(word) + r"\b", t):
            return kind
    return None


# ---------------------------------------------------------------- matching
def resolve_task(needle: str, tasks: list[Task]) -> list[Task]:
    """Candidate tasks for a phrase. Ambiguity is reported, never guessed."""
    needle = (needle or "").strip().lower()
    if not needle:
        return []
    exact = [t for t in tasks if t.id == needle]
    if exact:
        return exact
    open_tasks = [t for t in tasks if not t.done]

    def norm(s: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()

    # Digits matter: "essay 1" and "essay 2" differ only by a one-character token,
    # so a plain length filter would make every Purdue essay equally likely.
    words = [w for w in re.split(r"\W+", needle) if len(w) > 2 or w.isdigit()]
    needle_n = norm(needle)

    scored = []
    for t in open_tasks:
        hay = norm(f"{t.id} {t.title} {t.school}")
        hits = sum(1 for w in words if w in hay)
        if not hits:
            continue
        # The first word is usually the most specific one - a school name. Without
        # weighting it, "purdue essay 1" happily matches "Stanford - essay 1".
        if words and words[0] in hay:
            hits += len(words)
        # a whole-phrase hit outranks the same words scattered around
        if needle_n and needle_n in norm(t.title):
            hits += 2 * len(words) + 1
        scored.append((hits, -len(t.title), t))
    scored.sort(key=lambda x: (-x[0], x[1]))
    if not scored:
        return []
    best = scored[0][0]
    return [t for h, _, t in scored if h == best]


# ---------------------------------------------------------------- rules
def parse_rules(text: str, today: dt.date, tasks: list[Task]) -> Command | None:
    raw = text.strip()
    t = raw.lower()
    if not t:
        return None

    if re.fullmatch(r"(replan|re-?plan|refresh|update the plan)\.?", t):
        return Command("replan", {}, "rules", raw)

    m = re.match(r"^(?:add|new|create)\s+(.*)$", t)
    if m:
        body = m.group(1)
        due = parse_date(body, today)
        minutes = parse_minutes(body)
        kind = guess_kind(body) or "essay"
        title = _clean_title(m_group_original(raw, m.start(1)))
        if not title:
            raise ParseError("I got the date but not a title.")
        return Command("add", {
            "title": title, "kind": kind,
            "due": due.isoformat() if due else None,
            "estimate": minutes or DEFAULT_ESTIMATES.get(kind, 90),
        }, "rules", raw)

    m = re.match(r"^(?:done|finished|completed|did)\s+(?:the\s+|my\s+)?(.*)$", t)
    if m:
        body = m.group(1)
        minutes = parse_minutes(body)
        needle = _strip_time_words(body)
        return Command("done", {"task": needle, "minutes": minutes}, "rules", raw)

    m = re.match(r"^(?:skip|skipped|missed|bail(?:ed)? on)\s+(?:the\s+|my\s+)?(.*)$", t)
    if m:
        return Command("skip", {"task": _strip_time_words(m.group(1)), "reason": ""},
                       "rules", raw)

    m = re.match(r"^(?:move|push|shift|delay|postpone)\s+(?:the\s+|my\s+)?(.*?)"
                 r"\s+(?:to|until|till)\s+(.*)$", t)
    if m:
        when = parse_date(m.group(2), today)
        if not when:
            raise ParseError(f"I could not read '{m.group(2)}' as a date.")
        return Command("move", {"task": m.group(1).strip(), "to": when.isoformat()},
                       "rules", raw)

    m = re.match(r"^(?:block|block off|clear|take off|keep)\s+(.*?)\s*(?:off|free)?$", t)
    if m:
        body = m.group(1)
        rng = re.search(r"(.+?)\s*(?:-|to|through|until)\s*(.+)", body)
        if rng:
            a, b = parse_date(rng.group(1), today), parse_date(rng.group(2), today)
        else:
            a = b = parse_date(body, today)
        if not a or not b:
            raise ParseError(f"I could not read '{body}' as a date or range.")
        return Command("blackout", {"from": a.isoformat(), "to": b.isoformat(),
                                    "tiers": [2], "reason": raw}, "rules", raw)
    return None


def m_group_original(raw: str, start: int) -> str:
    return raw[start:]


def _clean_title(body: str) -> str:
    """Strip the date and duration phrases out of an add command's title."""
    s = body
    s = re.sub(r"\b(due|by|on|for)\b\s+", " ", s, flags=re.I)
    s = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", " ", s)
    s = re.sub(r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b", " ", s)
    s = re.sub(r"\b(" + "|".join(MONTHS) + r")[a-z]*\.?\s+\d{1,2}(?:st|nd|rd|th)?\b",
               " ", s, flags=re.I)
    s = re.sub(r"\bthe\s+\d{1,2}(?:st|nd|rd|th)\b", " ", s, flags=re.I)
    s = re.sub(r"\b(today|tonight|tomorrow|next week)\b", " ", s, flags=re.I)
    s = re.sub(r"\b(next\s+)?(" + "|".join(WEEKDAYS) + r")[a-z]*\b", " ", s, flags=re.I)
    s = _strip_time_words(s)
    s = re.sub(r"[,;]+", " ", s)
    return re.sub(r"\s{2,}", " ", s).strip(" ,.-")


def _strip_time_words(s: str) -> str:
    s = re.sub(r"\b(?:it\s+)?took\s+(?:me\s+)?(?:like\s+|about\s+)?", " ", s, flags=re.I)
    s = re.sub(r"\b\d+(?:\.\d+)?\s*(?:h|hr|hrs|hour|hours|m|min|mins|minute|minutes)\b",
               " ", s, flags=re.I)
    s = re.sub(r"\b(an hour and a half|an hour|maybe|roughly|around|about)\b", " ", s, flags=re.I)
    return re.sub(r"\s{2,}", " ", s).strip(" ,.-")


# ---------------------------------------------------------------- model
SYSTEM_PROMPT = """You convert a student's sentence into ONE JSON command for a scheduling app.
Reply with JSON only. No prose, no markdown fences.

Schema:
{"action":"add","title":str,"kind":"essay|schoolwork|test|build|admin","due":"YYYY-MM-DD"|null,"estimate":int_minutes}
{"action":"done","task":str,"minutes":int|null}
{"action":"skip","task":str,"reason":str}
{"action":"move","task":str,"to":"YYYY-MM-DD"}
{"action":"blackout","from":"YYYY-MM-DD","to":"YYYY-MM-DD","tiers":[2],"reason":str}
{"action":"anchor","id":str,"start":"HH:MM"|null,"end":"HH:MM"|null,"days":[str]|null}
{"action":"replan"}

"task" is a short phrase naming an existing task. Anchor ids: school, gym, club, dinner, call.
If the sentence does not map cleanly onto one command, reply {"action":"unknown"}."""


def call_model(text: str, cfg: dict[str, Any], today: dt.date) -> dict[str, Any]:
    """Ask the configured chat endpoint for a command. Stdlib only."""
    nlu = cfg.get("nlu", {}) or {}
    endpoint = nlu.get("endpoint")
    if not endpoint:
        raise ParseError("No model endpoint configured (config.nlu.endpoint).")

    payload = {
        "model": nlu.get("model", "qwen2.5:3b"),
        "temperature": 0,
        "stream": False,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT + f"\nToday is {today.isoformat()}."},
            {"role": "user", "content": text},
        ],
    }
    body = json.dumps(payload).encode()
    req = urllib.request.Request(endpoint, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    key_env = nlu.get("api_key_env", "CALIST_LLM_KEY")
    if key_env and os.environ.get(key_env):
        req.add_header("Authorization", "Bearer " + os.environ[key_env])

    try:
        with urllib.request.urlopen(req, timeout=float(nlu.get("timeout", 30))) as r:
            data = json.loads(r.read().decode())
    except urllib.error.URLError as e:
        raise ParseError(
            f"Could not reach the model at {endpoint} ({e.reason}). "
            "Is Ollama running? Try: ollama serve"
        ) from e
    except (ValueError, KeyError) as e:
        raise ParseError(f"The model returned something unreadable: {e}") from e

    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise ParseError("The model's reply had no message content.") from e
    return extract_json(content)


def extract_json(content: str) -> dict[str, Any]:
    """Pull one JSON object out of a model reply, fences and all."""
    text = content.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    start, depth = text.find("{"), 0
    if start < 0:
        raise ParseError("The model did not return JSON.")
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError as e:
                    raise ParseError(f"The model's JSON was malformed: {e}") from e
    raise ParseError("The model's JSON was never closed.")


def validate(obj: dict[str, Any], today: dt.date) -> Command:
    """Turn raw model output into a Command, or refuse it."""
    if not isinstance(obj, dict):
        raise ParseError("Expected a JSON object.")
    action = str(obj.get("action", "")).lower()
    if action == "unknown":
        raise ParseError("I could not turn that into a change.")
    if action not in ACTIONS:
        raise ParseError(f"'{action}' is not something I can do.")

    def date_or_none(key):
        v = obj.get(key)
        if v in (None, "", "null"):
            return None
        try:
            return dt.date.fromisoformat(str(v)[:10]).isoformat()
        except ValueError:
            raise ParseError(f"'{v}' is not a valid date for {key}.")

    if action == "add":
        title = str(obj.get("title") or "").strip()
        if not title:
            raise ParseError("An added task needs a title.")
        kind = str(obj.get("kind") or "essay")
        if kind not in DEFAULT_ESTIMATES:
            kind = "schoolwork"
        est = obj.get("estimate")
        est = int(est) if isinstance(est, (int, float)) and est > 0 else DEFAULT_ESTIMATES[kind]
        return Command("add", {"title": title, "kind": kind,
                               "due": date_or_none("due"),
                               "estimate": min(est, 8 * 60)}, "model")
    if action in ("done", "skip"):
        task = str(obj.get("task") or "").strip()
        if not task:
            raise ParseError(f"'{action}' needs to name a task.")
        params = {"task": task}
        if action == "done":
            mins = obj.get("minutes")
            params["minutes"] = int(mins) if isinstance(mins, (int, float)) and mins > 0 else None
        else:
            params["reason"] = str(obj.get("reason") or "")
        return Command(action, params, "model")
    if action == "move":
        to = date_or_none("to")
        if not to:
            raise ParseError("A move needs a target date.")
        return Command("move", {"task": str(obj.get("task") or "").strip(), "to": to}, "model")
    if action == "blackout":
        a, b = date_or_none("from"), date_or_none("to")
        if not a or not b:
            raise ParseError("A blackout needs a start and an end date.")
        if b < a:
            a, b = b, a
        tiers = obj.get("tiers") or [2]
        tiers = [int(x) for x in tiers if str(x).isdigit() or isinstance(x, int)]
        return Command("blackout", {"from": a, "to": b, "tiers": tiers or [2],
                                    "reason": str(obj.get("reason") or "")}, "model")
    if action == "anchor":
        aid = str(obj.get("id") or "").strip()
        if not aid:
            raise ParseError("Which commitment should change?")
        params = {"id": aid}
        for k in ("start", "end"):
            v = obj.get(k)
            if v:
                if not re.fullmatch(r"\d{1,2}:\d{2}", str(v)):
                    raise ParseError(f"'{v}' is not a time like 14:40.")
                params[k] = str(v)
        days = obj.get("days")
        if isinstance(days, list) and days:
            params["days"] = [str(d)[:3].lower() for d in days]
        if len(params) == 1:
            raise ParseError("Nothing to change on that commitment.")
        return Command("anchor", params, "model")
    return Command("replan", {}, "model")


def parse(text: str, cfg: dict[str, Any] | None = None,
          tasks: list[Task] | None = None, use_model: bool = True) -> Command:
    """Rules first; the model only for what the rules miss."""
    cfg = cfg or load_config()
    tasks = tasks if tasks is not None else load_tasks()
    today = clock.today(cfg)

    cmd = parse_rules(text, today, tasks)
    if cmd:
        return cmd
    if not use_model:
        raise ParseError("I did not understand that, and the model is switched off.")
    cmd = validate(call_model(text, cfg, today), today)
    cmd.text = text
    return cmd


# ---------------------------------------------------------------- describe
def describe(cmd: Command, tasks: list[Task] | None = None,
             cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """One plain sentence for the confirmation line, plus any ambiguity."""
    tasks = tasks if tasks is not None else load_tasks()
    cfg = cfg or load_config()
    p = cmd.params
    out: dict[str, Any] = {"action": cmd.action, "source": cmd.source,
                           "params": p, "choices": []}

    def pretty(d):
        return dt.date.fromisoformat(d).strftime("%a %b %-d")

    if cmd.action == "add":
        bits = [f"Add {p['kind']} “{p['title']}”"]
        if p.get("due"):
            bits.append("due " + pretty(p["due"]))
        bits.append(f"({p['estimate']} min)")
        out["summary"] = " ".join(bits)
        return out

    if cmd.action in ("done", "skip", "move"):
        matches = resolve_task(p.get("task", ""), tasks)
        if not matches:
            out["summary"] = f"No open task matches “{p.get('task')}”."
            out["error"] = "no match"
            return out
        if len(matches) > 1:
            out["summary"] = f"“{p.get('task')}” matches {len(matches)} tasks - which one?"
            out["choices"] = [{"id": t.id, "title": t.title} for t in matches[:8]]
            out["error"] = "ambiguous"
            return out
        task = matches[0]
        p["task_id"] = task.id
        if cmd.action == "done":
            idx = task.next_stage_index()
            stage = task.stages[idx].name if idx is not None else "?"
            mins = f", {p['minutes']} min" if p.get("minutes") else ""
            out["summary"] = f"Mark {task.title} [{stage}] done{mins}"
        elif cmd.action == "skip":
            out["summary"] = f"Log a skip for {task.title}"
        else:
            out["summary"] = f"Do not work on {task.title} before {pretty(p['to'])}"
        return out

    if cmd.action == "blackout":
        tiers = {1: "schoolwork", 2: "essays", 3: "creative time"}
        what = ", ".join(tiers.get(t, str(t)) for t in p.get("tiers", [2]))
        span = pretty(p["from"]) + (f" – {pretty(p['to'])}" if p["to"] != p["from"] else "")
        out["summary"] = f"Keep {span} clear of {what}"
        return out

    if cmd.action == "anchor":
        anchor = next((a for a in cfg.get("anchors", []) if a.get("id") == p["id"]), None)
        if not anchor:
            out["summary"] = f"No commitment called “{p['id']}”."
            out["error"] = "no match"
            return out
        changes = []
        if p.get("start") or p.get("end"):
            changes.append(f"{p.get('start', anchor['start'])}–{p.get('end', anchor['end'])}")
        if p.get("days"):
            changes.append("on " + ", ".join(p["days"]))
        out["summary"] = f"Change {anchor['name']} to " + " ".join(changes)
        return out

    out["summary"] = "Rebuild the schedule"
    return out


# ---------------------------------------------------------------- apply
def apply(cmd: Command, replan: bool = True) -> dict[str, Any]:
    """Execute a validated command. Returns what actually changed."""
    cfg = load_config()
    tasks = load_tasks()
    today = clock.today(cfg)
    p = cmd.params
    changed: dict[str, Any] = {"action": cmd.action}

    if cmd.action == "add":
        task = make_task(p["title"], tasks, cfg, kind=p["kind"], due=p.get("due"),
                         available_from=today.isoformat(), estimate=p["estimate"])
        tasks.append(task)
        save_tasks(tasks)
        changed["task_id"] = task.id
        changed["detail"] = f"added {task.title}"

    elif cmd.action in ("done", "skip", "move"):
        tid = p.get("task_id")
        if not tid:
            matches = resolve_task(p.get("task", ""), tasks)
            if len(matches) != 1:
                raise ParseError("That did not match exactly one task.")
            tid = matches[0].id
        task = next(t for t in tasks if t.id == tid)

        if cmd.action == "done":
            idx = task.next_stage_index()
            if idx is None:
                raise ParseError(f"{task.title} is already finished.")
            stage = task.stages[idx]
            stage.status = "done"
            stage.done_date = today.isoformat()
            stage.actual_minutes = p.get("minutes")
            save_tasks(tasks)
            log_event("done", task_id=task.id, stage=stage.name,
                      planned_minutes=stage.minutes, actual_minutes=p.get("minutes"),
                      scheduled_hour=clock.now(cfg).hour, via="say")
            changed["detail"] = f"{task.title} [{stage.name}] done"
        elif cmd.action == "skip":
            log_event("skip", task_id=task.id, reason=p.get("reason", ""),
                      scheduled_hour=clock.now(cfg).hour, via="say")
            changed["detail"] = f"skip logged for {task.title}"
        else:
            task.available_from = p["to"]
            if task.due and task.due < p["to"]:
                task.due = p["to"]
            save_tasks(tasks)
            changed["detail"] = f"{task.title} held until {p['to']}"

    elif cmd.action == "blackout":
        cfg.setdefault("blackouts", []).append({
            "from": p["from"], "to": p["to"], "tiers": p.get("tiers", [2]),
            "reason": p.get("reason", "") or "asked for",
        })
        save_config(cfg)
        changed["detail"] = f"{p['from']} to {p['to']} kept clear"

    elif cmd.action == "anchor":
        anchor = next((a for a in cfg.get("anchors", []) if a.get("id") == p["id"]), None)
        if not anchor:
            raise ParseError(f"No commitment called '{p['id']}'.")
        for k in ("start", "end", "days"):
            if p.get(k):
                anchor[k] = p[k]
        save_config(cfg)
        changed["detail"] = f"{anchor['name']} updated"

    log_event("say", command=cmd.to_json(), text=cmd.text)

    if replan:
        from . import calibrate, habits, icsio, planner
        from .store import ICS_PATH, PLAN_PATH, write_json
        from .models import to_jsonable

        result = planner.plan(load_tasks(), load_config(), __import__(
            "calist.store", fromlist=["load_state"]).load_state(),
            today=today, now_minutes=clock.minutes_now(cfg),
            multipliers=calibrate.multipliers(),
            hour_scores=habits.follow_through_by_hour())
        write_json(PLAN_PATH, to_jsonable(result))
        icsio.write_ics(result.blocks, ICS_PATH, name="Calist")
        changed["stats"] = result.stats
        changed["late"] = len(result.late)
        changed["unplaceable"] = len(result.unplaceable)
    return changed
