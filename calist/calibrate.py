"""Learning how long your work actually takes.

If your revisions consistently run 1.5x the estimate, future plans should
budget 1.5x. That is the whole idea - a number derived from your own history,
not a guess.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from .store import LOG_PATH, read_jsonl

MIN_SAMPLES = 3
CLAMP_LOW, CLAMP_HIGH = 0.6, 2.0


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def ratios(records: list[dict[str, Any]] | None = None) -> dict[str, list[float]]:
    records = read_jsonl(LOG_PATH) if records is None else records
    out: dict[str, list[float]] = defaultdict(list)
    for rec in records:
        if rec.get("type") != "done":
            continue
        planned = rec.get("planned_minutes")
        actual = rec.get("actual_minutes")
        stage = rec.get("stage")
        if not stage or not planned or not actual:
            continue
        try:
            ratio = float(actual) / float(planned)
        except (TypeError, ZeroDivisionError, ValueError):
            continue
        if 0 < ratio < 10:
            out[str(stage)].append(ratio)
    return dict(out)


def multipliers(records: list[dict[str, Any]] | None = None) -> dict[str, float]:
    """Per-stage estimate multipliers, median-based and clamped.

    Median rather than mean so one disastrous all-nighter does not permanently
    inflate every future estimate.
    """
    result: dict[str, float] = {}
    for stage, values in ratios(records).items():
        if len(values) < MIN_SAMPLES:
            continue
        result[stage] = round(max(CLAMP_LOW, min(CLAMP_HIGH, _median(values))), 2)
    return result


def explain(records: list[dict[str, Any]] | None = None) -> list[str]:
    lines = []
    data = ratios(records)
    mult = multipliers(records)
    for stage, values in sorted(data.items()):
        if stage in mult:
            pct = int(round((mult[stage] - 1) * 100))
            direction = "longer" if pct > 0 else "shorter"
            lines.append(
                f"{stage}: {len(values)} samples, you take {abs(pct)}% {direction} "
                f"than estimated (x{mult[stage]})"
            )
        else:
            lines.append(f"{stage}: only {len(values)} sample(s) - need {MIN_SAMPLES} to calibrate")
    return lines
