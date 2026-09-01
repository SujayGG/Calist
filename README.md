# Calist

A deadline planner that understands your actual day.

School ends at 2:40. You walk to the car, drive 17 minutes, and get home. Calist
knows your first work block starts at **3:12pm**, not 2:41. That is the whole
idea: a schedule made of time that really exists.

Built for one person, runs entirely on your own machine, and costs nothing.
**Python 3.11 standard library only** - no pip install, no accounts, no API keys.

## Quick start (Windows)

```bat
py -m calist setup --school-end 14:40 --drive 17 --settle 15 --target 2026-09-22
py -m calist add "Purdue - why this major" --due 2026-09-20 --estimate 120
py -m calist plan
py -m calist today
py -m calist serve
```

Then open http://127.0.0.1:8787.

On macOS/Linux use `python3 -m calist` instead of `py -m calist`.

## Talking to it

```bash
py -m calist say "done purdue essay 1, took 90 min"
py -m calist say "add AP Bio unit 3 test on the 14th, 4 hours"
py -m calist say "move stanford essay 1 to friday"
py -m calist say "block sept 11 - sept 15"
```

It shows what it understood, you confirm, it applies and replans. The same box
sits at the top of the dashboard.

**Most commands never touch a model.** A rules parser handles the everyday
phrasing instantly and offline. That matters because it keeps working when
nothing else is running.

**The optional local model** handles phrasing the rules miss. It is genuinely
optional and completely free:

```bash
# one time
winget install Ollama.Ollama     # or: brew install ollama
ollama pull qwen2.5:3b           # ~2GB
ollama serve
```

Point `nlu.endpoint` in `data/config.json` elsewhere and LM Studio, llama.cpp or
a hosted endpoint work too - anything speaking the OpenAI chat format. Only
Ollama is tested.

Two rules keep this safe. The model **never writes to your data**: it returns a
JSON command that is schema-validated here, shown to you as one sentence, then
executed by ordinary code. And an ambiguous instruction is **reported, never
guessed** - "purdue essay" matching three tasks asks which one rather than
picking. `--no-model` forces the rules-only path; `--yes` skips the confirmation.

## Using it on another device

The repo carries the schedule, so a second machine needs nothing special.

**Just want to see the calendar (2 minutes, no install):**
1. On GitHub open `data/plan.ics` and click **Download raw file**.
2. Google Calendar -> gear -> **Settings** -> **Import & export** -> **Import**,
   pick the file, choose a calendar, import.

Every block lands in your normal calendar, on your laptop and your phone. Re-import
after a replan to refresh. Times are floating, so they display in your local zone
with no timezone setup.

**On Windows** see [docs/WINDOWS.md](docs/WINDOWS.md) for the click-by-click version.

**Want the full dashboard:**
```bash
git clone https://github.com/SujayGG/Calist.git
cd Calist
python3 -m calist serve
```
Then open http://127.0.0.1:8787. Needs Python 3.9 or newer — already present on
macOS and most Linux; on Windows install from python.org and use `py` in place of
`python3`. There is nothing to `pip install`.

**Keeping two machines in sync.** `tasks.json`, `config.json` and the generated
plan are all committed, so:
```bash
git pull            # before you start working
git add -A && git commit -m "log progress" && git push   # when you finish
```
Skip that and the two machines will disagree about what you have done.

## What makes it different

**It models the friction.** Anchors (school, clubs, gym, dinner, the call with
your girlfriend) carry travel and settle buffers, and those are removed from the
day before any work is placed.

**It models your essay coach.** Essays run `draft -> [coach turnaround] -> revise
-> polish`. A revision is *unschedulable* until the coach has actually had the
draft for `coach_latency_days`. The daily cadence - one draft, one revision - is
a **cap**, because writing five drafts on a free Saturday just means five
revisions land on the same day later.

**It never lies about overcommitment.** Work that can't fit is reported as
`unplaceable`; work that fits only after its deadline is reported as `late`, with
the number of days and what you can do about it. It will also tell you when the
cadence, not your time, is the limit.

**It learns.** `calibrate` derives per-stage multipliers from what you actually
logged - if revisions take you 1.5x your estimate, future plans budget 1.5x.
`habits` turns phone usage and completions into your real follow-through by hour,
and work gets scheduled where you actually follow through.

**It protects what matters.** Dinner and the call are never scheduled over. Sleep
math is validated - it warns instead of quietly booking you 6-hour nights. The
gym ramps from 3 days a week rather than starting at 5 and collapsing. Building
things with Claude has a guaranteed weekly floor that survives replanning.

## Commands

| command | what it does |
|---|---|
| `setup` | configure your real week (school hours, drive, sleep, target) |
| `add` | add an essay, assignment, test or build project |
| `plan` | rebuild the schedule and write `plan.ics` |
| `today` | the day, with anchors and work blocks (`--days 3`) |
| `next` | what to do right now (`--json` for the phone) |
| `done` | mark a stage complete, with real minutes |
| `skip` | log a miss so follow-through stays honest |
| `status` | progress, and what Calist has learned about you |
| `why` | where a day's hours actually go |
| `import` | read an `.ics` (`--as-anchors` for recurring commitments) |
| `usage` | log phone time by hand |
| `ics` | re-export the calendar |
| `serve` | dashboard on :8787 |
| `watch` | distraction nudges (`--dry-run` to test safely) |

## The nudges

**Windows:** `py -m calist watch` polls the foreground window via ctypes and,
after 7 minutes in a watched app, shows an always-on-top window with your current
block and *On it* / *Snooze 5*. Instagram is usually a browser tab, so the
watchlist matches window titles as well as process names - edit `watch` in
`data/config.json`. Start with `--dry-run` to see what it detects without
anything popping up.

**Android:** see [docs/ANDROID.md](docs/ANDROID.md) - 4 free MacroDroid macros
that nudge you and log your real app usage back to the dashboard.

## Your data

Everything is plain text in `data/`, git-ignored by default:

| file | |
|---|---|
| `config.json` | your week, sleep, cadence, watchlist |
| `tasks.json` | tasks and their stages |
| `plan.json` | **generated** - never hand-edit, re-run `plan` |
| `plan.ics` | subscribe to this in Google Calendar |
| `log.jsonl` | completions, skips, nudges |
| `usage.jsonl` | phone app usage |
| `profile.md` | Claude's notes on how you actually work |

## Tests

```
python3 -m unittest discover tests -v
```

The Windows overlay and the Android macros are the only parts not covered - their
logic is tested behind a fake backend, and the platform calls are thin adapters.
