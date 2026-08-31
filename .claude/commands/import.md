---
description: Pull deadlines out of a syllabus or calendar file
---
For files the user dropped in `data/sources/`:

- `.ics` -> `python3 -m calist import data/sources/<file>.ics`
  (add `--as-anchors` for recurring commitments like clubs or class periods;
  remind him to check the travel buffers on anything imported as an anchor)
- **PDF / DOCX / images** -> read the file yourself with the Read tool, pull out
  every dated deliverable, and turn each into a `calist add` command. There is
  deliberately no PDF dependency in this project.

Show him the list of what you extracted BEFORE adding it, then add and replan.

$ARGUMENTS
