"""Shared fixtures for the test suite."""

import datetime as dt
import json

from calist.models import Task
from calist.store import DEFAULT_CONFIG, DEFAULT_STATE
from calist.tasking import make_task


def cfg(**overrides):
    c = json.loads(json.dumps(DEFAULT_CONFIG))
    c.update(overrides)
    return c


def state(**overrides):
    s = json.loads(json.dumps(DEFAULT_STATE))
    s.update(overrides)
    return s


def essays(n, due_start, config, minutes=120, spacing=2):
    """n essays with staggered deadlines."""
    out = []
    for i in range(n):
        due = (due_start + dt.timedelta(days=i * spacing)).isoformat()
        out.append(
            make_task(f"Essay {i + 1} why us", out, config, kind="essay", due=due, estimate=minutes)
        )
    return out


def blocks_on(result, day):
    return [b for b in result.blocks if b.date == day.isoformat()]
