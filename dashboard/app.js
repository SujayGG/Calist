"use strict";

const $ = (id) => document.getElementById(id);
let offset = 0;

const api = async (path, opts) => {
  const res = await fetch(path, opts);
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json();
};

const post = (path, body) =>
  api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );

const minutesBetween = (a, b) => {
  const [ah, am] = a.split(":").map(Number);
  const [bh, bm] = b.split(":").map(Number);
  return bh * 60 + bm - (ah * 60 + am);
};

function renderDay(day) {
  $("day-title").textContent = day.pretty;
  const rows = [];

  for (const a of day.anchors) {
    let meta = "";
    const extra = [];
    if (a.travel_after) extra.push(`+${a.travel_after}m travel`);
    if (a.settle_after) extra.push(`+${a.settle_after}m settle`);
    if (extra.length) meta = extra.join(", ");
    rows.push({
      sort: a.start,
      html: `<div class="row anchor">
        <div class="time">${esc(a.start_label)} &ndash; ${esc(a.end_label)}</div>
        <div class="what"><div class="name">${esc(a.name)}</div>
          ${meta ? `<div class="meta">${esc(meta)}</div>` : ""}</div>
        <div><span class="tag">fixed</span></div></div>`,
    });
  }

  for (const b of day.blocks) {
    const mins = minutesBetween(b.start, b.end);
    const cls = b.type === "build" ? "build" : "work";
    const stageTag = b.stage_name
      ? `<span class="tag stage">${esc(b.stage_name)}</span>`
      : `<span class="tag stage">creative</span>`;
    const action = b.task_id
      ? `<button data-task="${esc(b.task_id)}" data-stage="${esc(b.stage_name || "")}" data-min="${mins}">done</button>`
      : "";
    rows.push({
      sort: b.start,
      html: `<div class="row ${cls}">
        <div class="time">${esc(b.start_label)} &ndash; ${esc(b.end_label)}</div>
        <div class="what"><div class="name">${esc(b.title)}</div>
          <div class="meta">${mins} min${b.note ? " &middot; " + esc(b.note) : ""}</div></div>
        <div>${stageTag} ${action}</div></div>`,
    });
  }

  rows.sort((x, y) => x.sort.localeCompare(y.sort));
  $("timeline").innerHTML = rows.length
    ? rows.map((r) => r.html).join("")
    : `<div class="empty">Nothing scheduled. Run <code>calist plan</code>.</div>`;

  for (const btn of $("timeline").querySelectorAll("button[data-task]")) {
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      btn.textContent = "...";
      const actual = prompt("How many minutes did it actually take?", btn.dataset.min);
      if (actual === null) {
        btn.disabled = false;
        btn.textContent = "done";
        return;
      }
      await post("/api/done", {
        task_id: btn.dataset.task,
        stage: btn.dataset.stage || null,
        minutes: parseInt(actual, 10) || null,
      });
      await refresh();
    });
  }
}

function renderWeek(days) {
  $("week").innerHTML = days
    .map((d, i) => {
      const hours = (d.work_minutes / 60).toFixed(1);
      const date = new Date(d.date + "T12:00:00");
      return `<div class="d ${i === 0 ? "today" : ""}">
        <div class="dn">${date.toLocaleDateString(undefined, { weekday: "short" })}</div>
        <div class="dv">${date.getDate()}</div>
        <div class="dh">${d.work_minutes ? hours + "h" : "&mdash;"}</div>
      </div>`;
    })
    .join("");
}

function renderStatus(s) {
  $("days-left").textContent = s.days_to_target ?? "-";
  $("streak").textContent = s.streak ?? 0;

  const e = s.essays;
  const pct = (n) => (e.total ? (n / e.total) * 100 : 0);
  $("essay-meter").innerHTML =
    `<i class="c" style="width:${pct(e.done)}%"></i>` +
    `<i class="r" style="width:${pct(e.revised - e.done)}%"></i>` +
    `<i class="d" style="width:${pct(e.drafted - e.revised)}%"></i>`;
  $("essay-legend").innerHTML =
    `<span><b>${e.done}</b> done</span><span><b>${e.revised}</b> revised</span>` +
    `<span><b>${e.drafted}</b> drafted</span><span><b>${e.total}</b> total</span>`;

  $("idle").textContent = (s.stats.idle_hours_next_week ?? 0) + "h";
  $("late-count").textContent = s.stats.late ?? 0;

  const schools = Object.entries(s.by_school || {});
  $("schools").innerHTML = schools.length
    ? schools
        .map(([name, v]) => {
          const p = v.total ? (v.done / v.total) * 100 : 0;
          return `<div><span>${esc(name)}</span>
            <span class="bar"><i style="width:${p}%"></i></span>
            <span class="n">${v.done}/${v.total}</span></div>`;
        })
        .join("")
    : `<div class="empty">No essays yet.</div>`;

  const learned = [];
  for (const line of s.calibration || []) learned.push(esc(line));
  const h = s.habits || {};
  if (h.has_usage_data) {
    learned.push(`<b>${h.social_minutes_per_day}</b> min/day on social apps`);
    if (h.worst_hours?.length) {
      learned.push(
        "riskiest hours: " +
          h.worst_hours.map((x) => `${x.hour}:00 (${Math.round(x.minutes)}m)`).join(", ")
      );
    }
  }
  if (h.best_work_hours?.length) {
    learned.push(
      "you follow through best at " +
        h.best_work_hours.map((x) => `${x.hour}:00 (${Math.round(x.rate * 100)}%)`).join(", ")
    );
  }
  $("learned").innerHTML = learned.length
    ? learned.map((l) => `<li>${l}</li>`).join("")
    : `<li>Not enough history yet. Log a few sessions with <code>calist done</code>.</li>`;

  const items = [];
  for (const l of s.late || [])
    items.push(`<b>${esc(l.title)}</b> &mdash; ${esc(l.stage)} is ${l.days_late}d late`);
  for (const u of s.unplaceable || [])
    items.push(`<b>${esc(u.title)}</b> &mdash; ${esc(u.reason)}`);
  $("attention").innerHTML = items.length
    ? items.map((l) => `<li>${l}</li>`).join("")
    : `<li>Nothing overdue or unschedulable.</li>`;

  $("banners").innerHTML = (s.warnings || [])
    .map((w) => `<div class="banner"><b>Heads up:</b> ${esc(w)}</div>`)
    .join("");
}

async function refresh() {
  const [day, week, status] = await Promise.all([
    api(`/api/today?offset=${offset}`),
    api("/api/week"),
    api("/api/status"),
  ]);
  $("today-label").textContent = new Date().toLocaleDateString(undefined, {
    weekday: "long",
    month: "long",
    day: "numeric",
  });
  renderDay(day);
  renderWeek(week.days);
  renderStatus(status);
}

$("prev").onclick = () => { offset -= 1; refresh(); };
$("next").onclick = () => { offset += 1; refresh(); };
$("today-btn").onclick = () => { offset = 0; refresh(); };
$("replan").onclick = async () => { await post("/api/plan", {}); refresh(); };

refresh().catch((err) => {
  document.getElementById("timeline").innerHTML =
    `<div class="empty">Could not load: ${esc(err.message)}</div>`;
});
setInterval(() => refresh().catch(() => {}), 60000);
