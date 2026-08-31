---
description: Rebuild the schedule and explain what changed
---
Run `python3 -m calist plan` and report back:

1. What moved compared to before, not just "done".
2. Any `late` or `unplaceable` items - state them plainly with the options
   the command printed. Never soften or omit these.
3. If the warning about idle capacity appears, tell him the cadence in
   `config.json` is the knob, and what raising it would buy.

Then show today and tomorrow with `python3 -m calist today --days 2`.
