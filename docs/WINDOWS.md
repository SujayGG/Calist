# Running Calist on your Windows PC

Everything below is free. There is nothing to `pip install`, ever.

---

## Step 1 — Install Python (5 minutes, one time)

1. Go to **https://www.python.org/downloads/windows/** and download the latest
   **Windows installer (64-bit)**.
2. Run it. On the very first screen, tick **"Add python.exe to PATH"** at the
   bottom. This is the step everyone misses and everything else depends on it.
3. Click **Customize installation** and make sure **"tcl/tk and IDLE"** stays
   ticked. That is what draws the distraction pop-up later. It is on by default.
4. Finish the install.

Check it worked. Press `Win + R`, type `cmd`, press Enter, then:

```bat
py --version
```

You should see `Python 3.12.x` or similar. Anything 3.9 or newer is fine.

> If you get *"py is not recognized"*, PATH was not ticked. Re-run the installer,
> choose **Modify**, and tick it.

---

## Step 2 — Get the project

Install **Git for Windows** from https://git-scm.com/download/win (all defaults
are fine), then in `cmd`:

```bat
cd %USERPROFILE%\Documents
git clone https://github.com/SujayGG/Calist.git
cd Calist
git checkout claude/essay-deadline-tracker-2dgzww
```

Your schedule is already inside — `data/tasks.json`, `data/config.json` and the
current plan all come with the clone. Nothing to re-enter.

*No Git?* Download the ZIP from the repo's green **Code** button instead, extract
it to Documents, and `cd` into the folder. You will lose the sync in Step 6.

---

## Step 3 — See your schedule

```bat
py -m calist today
py -m calist today --days 3
py -m calist next
```

If `today` prints your day, you are done setting up. Everything below is optional.

---

## Step 4 — The dashboard

```bat
py -m calist serve
```

Leave that window open and go to **http://127.0.0.1:8787** in your browser.
Tick work off as you finish it. `Ctrl + C` in the window stops it.

---

## Step 5 — Change things by typing

```bat
py -m calist say "done purdue essay 1, took 90 min"
py -m calist say "add AP Bio unit 3 test on the 14th, 4 hours"
py -m calist say "move stanford essay 1 to friday"
py -m calist say "block sept 11 - sept 15"
```

It shows what it understood and asks before changing anything. The same box sits
at the top of the dashboard.

Full command list: `py -m calist --help`. The ones worth knowing:

| command | what it does |
|---|---|
| `today` | your day (`--days 3` for more) |
| `next` | what to do right now |
| `done <task> --minutes 90` | log finished work |
| `skip <task>` | log a miss, honestly |
| `plan` | rebuild the schedule |
| `status` | progress + what it has learned about you |
| `why` | where a day's hours actually go |
| `add "..." --due 2026-09-20` | add work |
| `say "..."` | all of the above, in plain English |

---

## Step 6 — Keep your PC and laptop in step

Your tasks and plan live in the repo, so:

Before you start:

```bat
git pull
```

When you finish:

```bat
git add -A
git commit -m "progress"
git push
```

Skip this and the two machines will disagree about what you have done.

---

## Optional — the distraction nudge

```bat
py -m calist watch --dry-run
```

Dry run prints what it detects and never interrupts you. Watch it for a few
minutes; if it correctly spots Instagram or WhatsApp, drop `--dry-run` to arm it.
After 7 minutes in a watched app it puts a window on top with your current task
and *On it* / *Snooze 5*. Edit the `watch` section of `data\config.json` to change
the app list or the timing.

---

## Optional — the local AI model

Only needed for phrasing the built-in parser misses. Everything in Step 5 works
without it.

1. Install **Ollama** from https://ollama.com/download/windows
2. Download the model (about 2 GB):
   ```bat
   ollama pull qwen2.5:3b
   ```

**Do not run `ollama serve`.** The Windows installer already runs Ollama in the
background, so that command fails with *"Only one usage of each socket address..."*
- which looks alarming but just means it is already working.

Confirm the whole chain in one step:

```bat
py -m calist say --check
```

That sends a real request through the same code path a command uses, and prints
the endpoint, the model, and whether it answered. `CONNECTED` means you are done.

Use `py -m calist say --no-model "..."` to force the offline parser.

---

## If something breaks

| symptom | fix |
|---|---|
| `py is not recognized` | Python installer → Modify → tick "Add python.exe to PATH" |
| `No module named calist` | You are in the wrong folder. `cd %USERPROFILE%\Documents\Calist` |
| Dashboard won't load | Is the `serve` window still open? Use `127.0.0.1:8787`, not `localhost:8787`, if your browser is fussy |
| `say` says it can't reach the model | Run `py -m calist say --check`. If Ollama is not running, reopen the Ollama app from the Start menu. `--no-model` works meanwhile |
| `ollama serve` says the address is already in use | Ollama is already running - that is the background service the installer set up. Skip the command; run `py -m calist say --check` |
| A command matches several tasks | Name one of the ids it lists, or add `--all` to apply to every match |
| Nudge never appears | Run `py -m calist watch --dry-run` first and check it detects the app at all |
| Dates look wrong by one day | Check `timezone` in `data\config.json`; it should be `standard_offset_hours: -6` |
