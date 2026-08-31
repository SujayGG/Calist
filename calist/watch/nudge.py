"""Showing the nudge.

Primary: a Tkinter always-on-top window. tkinter ships with Python on Windows,
and unlike a toast it does not vanish while you keep scrolling.
Fallback: a PowerShell toast, then plain stdout.
"""

from __future__ import annotations

import subprocess
from typing import Any

WIDTH, HEIGHT = 460, 250


def show_nudge(app_label: str, focus: dict[str, Any]) -> str:
    """Return 'accept', 'snooze' or 'dismiss'."""
    try:
        return _tk_nudge(app_label, focus)
    except Exception:
        pass
    try:
        _toast(app_label, focus)
        return "dismiss"
    except Exception:
        print(f"\n[calist] {app_label}: {focus.get('headline', '')}")
        return "dismiss"


def _tk_nudge(app_label: str, focus: dict[str, Any]) -> str:
    import tkinter as tk

    result = {"action": "dismiss"}
    root = tk.Tk()
    root.title("Calist")
    root.attributes("-topmost", True)
    root.resizable(False, False)

    screen_w = root.winfo_screenwidth()
    root.geometry(f"{WIDTH}x{HEIGHT}+{(screen_w - WIDTH) // 2}+70")
    root.configure(bg="#1c1b19")

    def pad(widget, **kw):
        widget.pack(**kw)
        return widget

    pad(
        tk.Label(root, text=f"{app_label} for a while now",
                 font=("Segoe UI", 11), fg="#8b857c", bg="#1c1b19"),
        pady=(22, 4),
    )
    pad(
        tk.Label(root, text=focus.get("headline", "Get back to it"),
                 font=("Segoe UI", 16, "bold"), fg="#f7f6f3", bg="#1c1b19",
                 wraplength=WIDTH - 60, justify="center"),
        pady=(0, 6),
    )
    detail = focus.get("detail") or focus.get("next_deadline") or ""
    if detail:
        pad(
            tk.Label(root, text=detail, font=("Segoe UI", 10),
                     fg="#b45309", bg="#1c1b19", wraplength=WIDTH - 60, justify="center"),
            pady=(0, 16),
        )

    row = tk.Frame(root, bg="#1c1b19")
    row.pack(pady=6)

    def choose(action: str) -> None:
        result["action"] = action
        root.destroy()

    tk.Button(row, text="On it", width=12, relief="flat", bg="#b45309", fg="#ffffff",
              font=("Segoe UI", 10, "bold"), command=lambda: choose("accept")).pack(side="left", padx=6)
    tk.Button(row, text="Snooze 5 min", width=14, relief="flat", bg="#35343d", fg="#f7f6f3",
              font=("Segoe UI", 10), command=lambda: choose("snooze")).pack(side="left", padx=6)

    root.after(45000, root.destroy)  # never block the screen forever
    root.lift()
    root.attributes("-topmost", True)
    try:
        root.focus_force()
    except Exception:
        pass
    root.mainloop()
    return result["action"]


def _toast(app_label: str, focus: dict[str, Any]) -> None:
    """Windows toast via PowerShell - no modules to install."""
    title = f"Calist - {app_label}"
    body = focus.get("headline", "Back to work")
    script = f"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null
$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent(
  [Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$n = $t.GetElementsByTagName("text")
$n.Item(0).AppendChild($t.CreateTextNode({title!r})) | Out-Null
$n.Item(1).AppendChild($t.CreateTextNode({body!r})) | Out-Null
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("Calist").Show(
  [Windows.UI.Notifications.ToastNotification]::new($t))
""".replace("'", '"')
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, timeout=10, check=False,
    )
