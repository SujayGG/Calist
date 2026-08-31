"""The distraction nudge watcher.

Pure timing logic lives in DwellTracker so it can be tested without a GUI or a
Windows machine; the platform bits are isolated in backends.py and nudge.py.
"""

from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from ..store import load_config, log_event
from .backends import matches_watchlist, pick_backend


@dataclass
class DwellTracker:
    """Decides when a nudge is due.

    Rules: nudge after `dwell_minutes` of continuous time in a watched app;
    never again within `cooldown_minutes` for that same app; a snooze silences
    everything until it expires. Switching apps resets the dwell clock.
    """

    dwell_minutes: float = 7
    cooldown_minutes: float = 20
    current: str = ""
    since: float | None = None
    snooze_until: float = 0.0
    last_nudge: dict[str, float] = field(default_factory=dict)

    def snooze(self, now: float, minutes: float) -> None:
        self.snooze_until = now + minutes * 60

    def tick(self, now: float, label: str) -> bool:
        if not label:
            self.current, self.since = "", None
            return False

        if label != self.current:
            self.current, self.since = label, now
            return False

        if self.since is None:
            self.since = now
            return False

        if now - self.since < self.dwell_minutes * 60:
            return False
        if now < self.snooze_until:
            return False
        last = self.last_nudge.get(label, 0.0)
        if last and now - last < self.cooldown_minutes * 60:
            return False

        self.last_nudge[label] = now
        self.since = now  # restart the dwell clock after firing
        return True


def run_watcher(
    dry_run: bool = False,
    once: bool = False,
    backend: Any = None,
    clock: Callable[[], float] | None = None,
) -> int:
    cfg = load_config()
    watch_cfg = cfg.get("watch", {})
    backend = backend or pick_backend()
    clock = clock or time.time

    tracker = DwellTracker(
        dwell_minutes=float(watch_cfg.get("dwell_minutes", 7)),
        cooldown_minutes=float(watch_cfg.get("cooldown_minutes", 20)),
    )
    poll = float(watch_cfg.get("poll_seconds", 5))
    processes = watch_cfg.get("processes", [])
    patterns = watch_cfg.get("title_patterns", [])

    from ..cli import current_focus

    backend_name = type(backend).__name__
    print(f"Calist watcher running ({backend_name}).")
    print(f"  nudging after {tracker.dwell_minutes} min, cooldown {tracker.cooldown_minutes} min")
    if dry_run:
        print("  DRY RUN - detections are printed, nothing pops up")
    if backend_name == "NullBackend":
        print("  ! No foreground detection on this platform - run this on your laptop.")
    print("  Ctrl-C to stop.\n")

    try:
        while True:
            proc, title = backend.current()
            label = matches_watchlist(proc, title, processes, patterns)
            now = clock()

            if dry_run and label:
                # explicit None check: a timestamp of 0 is falsy but valid
                base = tracker.since if tracker.since is not None else now
                held = int((now - base) // 60)
                print(f"  [{dt.datetime.now():%H:%M:%S}] {label:<14} {held}m  ({proc} | {title[:44]})")

            if tracker.tick(now, label):
                focus = current_focus()
                log_event("nudge", app=label, state=focus.get("state"))
                if dry_run:
                    print(f"  >> WOULD NUDGE: {focus['headline']}")
                else:
                    from .nudge import show_nudge

                    action = show_nudge(label, focus)
                    if action == "snooze":
                        tracker.snooze(clock(), float(watch_cfg.get("snooze_minutes", 5)))
                        log_event("snooze", app=label)
                    elif action == "accept":
                        log_event("nudge_accepted", app=label)

            if once:
                return 0
            time.sleep(poll)
    except KeyboardInterrupt:
        print("\nstopped")
        return 0
