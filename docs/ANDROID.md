# Phone setup (Android) - free, ~20 minutes

Two jobs: **nudge you** when you've been in a feed too long, and **log your
real usage** so the planner learns your risk hours.

Everything here is free. MacroDroid's free tier allows 5 macros; this uses 4.

## Before you start

On your laptop, run the dashboard so the phone can reach it:

```
python3 -m calist serve --host 0.0.0.0
```

Find your laptop's LAN IP (`ipconfig` on Windows, look for IPv4 Address),
e.g. `192.168.1.24`. Your base URL is `http://192.168.1.24:8787`.

**Set a token first.** Binding to `0.0.0.0` puts your schedule on the whole
network with no login. In `data/config.json`:

```json
"server": { "host": "0.0.0.0", "port": 8787, "token": "pick-something-random" }
```

Then append `?t=pick-something-random` to every URL below.

## Macro 1 - the nudge

- **Trigger:** Applications > Application Launched > pick Instagram, TikTok,
  YouTube, WhatsApp
- **Actions:**
  1. `Wait Before Next Action` - 7 minutes
  2. `HTTP Request` - GET `http://192.168.1.24:8787/api/now?format=text&t=TOKEN`,
     save response to variable `task`
  3. `Notification` - title "Get back to it", text `[v=task]`
- **Constraint:** Application > *the same apps* > "Application is in foreground"

The constraint is what makes this work: if you closed Instagram within the
7 minutes, nothing fires.

If the laptop is off the HTTP step fails - add a second Notification action with
a fixed message like "You have essays due" and constrain it to run only on failure.

## Macro 2 - log when you open a watched app

- **Trigger:** Application Launched (same list)
- **Action:** `Write to File` - append to `calist_usage.jsonl`:
  ```
  {"ts":"[dd]-[MM]-[yyyy]T[hh]:[mm]:[ss]","app":"[app_name]","event":"open"}
  ```
  Use MacroDroid's date/time variables so the timestamp is real.

## Macro 3 - log when you close it

Same as macro 2 with the **Application Closed** trigger and `"event":"close"`.

## Macro 4 - upload when you get home

- **Trigger:** Connectivity > WiFi Connected > your home network
- **Actions:**
  1. `Read File` - `calist_usage.jsonl` into variable `body`
  2. `HTTP Request` - POST to `http://192.168.1.24:8787/api/usage?t=TOKEN`,
     content type `application/json`, body `[v=body]`
  3. `Write to File` - overwrite `calist_usage.jsonl` with empty content

The server accepts either a single JSON object or an array, so a batch upload
works. Events buffer on the phone while you're at school and sync when you're home.

## If the macros break

Nothing is lost - just tell Claude at check-in:

```
python3 -m calist usage instagram 180 --hours 20,21,22
```

The planner treats manual and automatic data the same way.

## The calendar

Export with `python3 -m calist plan` (writes `data/plan.ics`), then in Google
Calendar: **Other calendars -> Import**, pick `data/plan.ics`. Re-import after a
replan, or point a subscription at the file if you sync it somewhere.

## What is NOT possible for free

A true always-on overlay that draws over other apps needs a real installed app
using `UsageStatsManager` + `SYSTEM_ALERT_WINDOW`. That means Android Studio and
sideloading. The macro above is a notification, not an overlay - it is the honest
free version, and it works today.
