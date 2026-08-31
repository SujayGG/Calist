---
description: Log progress, update memory, recalibrate
---
Walk through a check-in:

1. Ask what he finished, and roughly how long each thing took.
2. Log each with `python3 -m calist done <task> --minutes N`. Log misses with
   `skip` - honest data matters more than a clean streak.
3. If he mentions phone time, log it: `python3 -m calist usage instagram 180 --hours 20,21,22`
4. Run `python3 -m calist status` and tell him what changed in the calibration
   numbers and follow-through hours.
5. Append anything durable you learned to `data/profile.md` - when he really
   starts working, what he underestimates, which excuses recur. Keep it terse.
6. Replan and report anything late or unplaceable.
