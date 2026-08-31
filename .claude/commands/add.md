---
description: Add work by describing it in plain language
---
Turn what the user said into `python3 -m calist add` calls.

- Infer `--kind` (essay/schoolwork/test/build/admin), `--due`, `--estimate`
  and `--school` from their wording. Essays default to 120 minutes.
- Tests should use `--kind test` so review gets spread over several days.
- If a due date is genuinely ambiguous, ask. Don't invent deadlines.
- Add everything first, then run `python3 -m calist plan` once and report the
  result, including anything late or unplaceable.

$ARGUMENTS
