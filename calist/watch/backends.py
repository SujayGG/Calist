"""Reading the foreground window.

The Win32 call is deliberately a thin adapter over a pure interface so the
dwell/snooze logic above it can be unit-tested on any platform. FakeBackend is
what the test suite drives.
"""

from __future__ import annotations

import platform
import re
import shutil
import subprocess
from typing import Protocol


class ForegroundBackend(Protocol):
    def current(self) -> tuple[str, str]:
        """Return (process_name, window_title). Empty strings when unknown."""


class FakeBackend:
    """Scripted foreground states, for tests and --dry-run demos."""

    def __init__(self, sequence: list[tuple[str, str]]):
        self.sequence = list(sequence)
        self.index = 0

    def current(self) -> tuple[str, str]:
        if not self.sequence:
            return ("", "")
        value = self.sequence[min(self.index, len(self.sequence) - 1)]
        self.index += 1
        return value


class WindowsBackend:
    """ctypes over user32/kernel32. No pywin32, no pip install."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes

        self.ctypes = ctypes
        self.wintypes = wintypes
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    def current(self) -> tuple[str, str]:
        ctypes, wintypes = self.ctypes, self.wintypes
        hwnd = self.user32.GetForegroundWindow()
        if not hwnd:
            return ("", "")

        length = self.user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        self.user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value or ""

        pid = wintypes.DWORD()
        self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        proc = ""
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = self.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if handle:
            try:
                size = wintypes.DWORD(4096)
                path = ctypes.create_unicode_buffer(size.value)
                if self.kernel32.QueryFullProcessImageNameW(
                    handle, 0, path, ctypes.byref(size)
                ):
                    proc = path.value.replace("\\", "/").split("/")[-1]
            finally:
                self.kernel32.CloseHandle(handle)
        return (proc, title)


class LinuxBackend:
    """Best effort via xdotool - mainly so --dry-run is useful off Windows."""

    def current(self) -> tuple[str, str]:
        if not shutil.which("xdotool"):
            return ("", "")
        try:
            title = subprocess.run(
                ["xdotool", "getactivewindow", "getwindowname"],
                capture_output=True, text=True, timeout=2,
            ).stdout.strip()
        except (subprocess.SubprocessError, OSError):
            return ("", "")
        return ("", title)


class NullBackend:
    def current(self) -> tuple[str, str]:
        return ("", "")


def pick_backend() -> ForegroundBackend:
    system = platform.system()
    if system == "Windows":
        try:
            return WindowsBackend()
        except Exception:
            return NullBackend()
    if system == "Linux":
        return LinuxBackend()
    return NullBackend()


def matches_watchlist(proc: str, title: str, processes: list[str], patterns: list[str]) -> str:
    """Return the matched label, or '' when the foreground app is fine.

    Instagram is normally a browser tab, so titles are matched as well as
    process names.
    """
    low_proc = (proc or "").lower()
    for name in processes or []:
        if name and low_proc == name.lower():
            return name
    haystack = f"{proc} {title}"
    for pattern in patterns or []:
        try:
            if re.search(pattern, haystack):
                m = re.search(pattern, haystack)
                return (m.group(0) if m else pattern).strip()
        except re.error:
            continue
    return ""
