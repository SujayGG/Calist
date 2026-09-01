# Calist - operating instructions for Claude

Calist is Sujay's personal deadline planner. **Talking to you is the input method.**
He should never have to hand-edit JSON; he says what changed and you run the CLI.

## Ground rules

1. **`data/plan.json` is generated.** Never hand-edit it. Change `tasks.json` or
   `config.json`, then run `python3 -m calist plan`.
2. **Always replan after changing anything**, and show him what actually moved -
   not just "done".
3. **Never hide overcommitment.** If `plan` reports `late` or `unplaceable`
   items, say so plainly and give him the options it printed. A schedule that
   looks fine but misses a deadline is the failure mode this whole tool exists
   to prevent.
4. **Zero dependencies.** Python 3.11 stdlib only. Do not add a pip install to
   this project, ever. No `zoneinfo` either - all times are naive local.
5. Everything here is free and stays free. No paid services, no API keys.

## The commands you'll use

```bash
python3 -m calist add "Purdue - why this major" --due 2026-09-20 --estimate 120
python3 -m calist add "AP Bio unit 3 test" --kind test --due 2026-09-14 --estimate 240
python3 -m calist plan                 # rebuild + write plan.ics
python3 -m calist today --days 3       # what the next few days look like
python3 -m calist next                 # what to do right now
python3 -m calist done purdue --minutes 85    # log real time spent
python3 -m calist skip purdue --reason "swim meet"
python3 -m calist status               # progress + what it has learned
python3 -m calist why --offset 1       # where tomorrow's hours actually go
python3 -m calist usage instagram 140 --hours 20,21,22
python3 -m calist import data/sources/school.ics --as-anchors
```

`--kind` is one of: `essay`, `schoolwork`, `test`, `build`, `admin`.

```bash
python3 -m calist say "done purdue essay 1, took 90 min"   # plain-language changes
```

## Two things that are easy to get wrong

- **`available_from` is not optional.** Every school task needs the date the work
  is assigned, the lab happens, or the material is taught. Without it the planner
  schedules next week's homework today - it did exactly that, and he caught it.
  When you add a task from a syllabus, set `available_from` as well as `due`.
- **Dates come from `calist.clock`, never `date.today()`.** He is US Central and
  the tool also runs in a UTC container; a plan built after 7pm his time would
  otherwise be dated tomorrow. `clock.today(cfg)` derives local time from UTC and
  handles US daylight saving.

## Turning what he says into commands

- *"I have a Purdue essay about my major due the 20th, maybe two hours"*
  -> `add "Purdue - why this major" --due 2026-09-20 --estimate 120`
- *"I finished the Purdue draft, took me like an hour and a half"*
  -> `done purdue --minutes 90`
- *"I have a bio test on the 14th"*
  -> `add "AP Bio unit 3 test" --kind test --due 2026-09-14 --estimate 240`
  (the `test` template spreads review across several days automatically)
- *"school gets out at 2:15 on Wednesdays now"*
  -> edit the school anchor in `config.json`, then replan.
- *"I was on Instagram like 3 hours yesterday, mostly at night"*
  -> `usage instagram 180 --hours 20,21,22`

**Syllabus PDFs**: read them yourself with the Read tool (it handles PDFs) and
turn each deadline into an `add` command. There is deliberately no PDF library.
`.ics` files go through `import`, which has a real parser.

## Things to remember about how this is built

- **The coach loop.** Essays are `draft -> [coach turnaround] -> revise -> polish`.
  The revise stage is unschedulable until `coach_latency_days` after the draft is
  marked done. If he's short on revisions, he isn't drafting fast enough.
- **The cadence is a cap, not just a target.** `config.cadence` is 1 draft +
  1 revise a day, because the coach can only turn around so much. If he wants to
  go faster, raise it there - and tell him that's the knob.
- **Anchors carry travel and settle buffers.** School ends 14:40, +17m drive,
  +15m settle, so work starts 15:12. If he says a commitment moved, update the
  buffers too.
- **The gym is a ramping habit** (`state.gym_sessions_per_week`), not a fixed
  block. Don't jump it to 5 days; let the streak earn it.
- **Sleep math is validated.** `calist setup` warns when a wake/cutoff pair drops
  under 7h. Never silently schedule him into 6-hour nights.
- **Creative time is a protected floor, earned.** Building things with Claude is
  his lowest priority but he loves it, so it has a weekly minimum that survives
  replanning. Don't let essays eat it permanently.

## Updating memory

`data/profile.md` is your long-term memory of how he actually works. Append
observations at check-in - when he really starts working, what he underestimates,
what excuses recur. Keep it short and specific; it is context you re-read, not a
diary.
