"""Open Chrome on the kit's own saved-login profile.

The login lives in a DEDICATED Chrome profile folder (`.chrome-profile/`), not in exported cookies.
Google invalidates its `__Secure-` / `__Host-` cookies when they reappear in a different browser, so
copying cookies around does not survive. A real profile folder does: sign in once, reuse it forever.

This profile is NOT the user's everyday Chrome. Quitting it is always safe.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from typing import Optional

from . import config

PAGE_TIMEOUT = 45


def _run(cmd: list[str]) -> str:
    kw = {"capture_output": True, "text": True, "timeout": 8}
    if config.IS_WINDOWS:
        kw["creationflags"] = 0x08000000      # CREATE_NO_WINDOW: no console flash
    try:
        return subprocess.run(cmd, **kw).stdout or ""
    except Exception:
        return ""


def chrome_path() -> Optional[str]:
    """Where Google Chrome is installed, or None."""
    if config.IS_MAC:
        p = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        from pathlib import Path
        for cand in (Path(p), Path.home() / p.lstrip("/")):
            if cand.exists():
                return str(cand)
        return None
    if config.IS_WINDOWS:
        import os
        for base in (os.environ.get("PROGRAMFILES", r"C:\Program Files"),
                     os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
                     os.environ.get("LOCALAPPDATA", "")):
            if base:
                cand = os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")
                if os.path.exists(cand):
                    return cand
        return None
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            return found
    return None


def chrome_major() -> Optional[int]:
    """Installed Chrome's major version, so the matching driver gets downloaded.

    A mismatch between Chrome and its driver is the most common reason automation will not start.
    """
    if config.IS_WINDOWS:
        for hive in ("HKCU", "HKLM"):
            out = _run(["reg", "query", hive + r"\Software\Google\Chrome\BLBeacon", "/v", "version"])
            m = re.search(r"REG_SZ\s+(\d+)\.", out)
            if m:
                return int(m.group(1))
        return None
    path = chrome_path()
    if not path:
        return None
    m = re.search(r"(\d+)\.\d+\.\d+", _run([path, "--version"]))
    return int(m.group(1)) if m else None


def _quiet_driver_service() -> None:
    """On Windows, stop chromedriver from flashing a black console window on every run."""
    if not config.IS_WINDOWS:
        return
    import selenium.webdriver.chromium.service as svc
    if getattr(svc, "_kit_no_window", False):
        return
    orig = svc.ChromiumService.__init__

    def quiet_init(self, *a, **kw):
        kw.setdefault("popen_kw", {})
        kw["popen_kw"]["creation_flags"] = 0x08000000
        orig(self, *a, **kw)

    svc.ChromiumService.__init__ = quiet_init
    svc._kit_no_window = True


def _options(uc, headless: bool, offscreen: bool):
    opts = uc.ChromeOptions()
    for arg in ("--no-first-run", "--no-default-browser-check", "--disable-notifications",
                "--disable-popup-blocking", "--lang=en-US"):
        opts.add_argument(arg)
    if headless:
        opts.add_argument("--headless=new")
        opts.add_argument("--window-size=1400,1000")
    elif offscreen:
        opts.add_argument("--window-position=-32000,-32000")
        opts.add_argument("--window-size=1400,1000")
    else:
        opts.add_argument("--start-maximized")
    return opts


def _kill_profile_chrome() -> None:
    """After a failed start, close any Chrome still holding THIS kit's profile (never the user's own
    Chrome), so the retry does not find the profile locked."""
    marker = str(config.PROFILE_DIR)
    try:
        if config.IS_WINDOWS:
            out = _run(["powershell", "-NoProfile", "-Command",
                        "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
                        "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"])
            import json
            procs = json.loads(out or "[]")
            procs = procs if isinstance(procs, list) else [procs]
            for p in procs:
                if marker.lower() in (p.get("CommandLine") or "").lower():
                    _run(["taskkill", "/PID", str(p["ProcessId"]), "/T", "/F"])
        else:
            _run(["pkill", "-f", f"user-data-dir={marker}"])
    except Exception:
        pass


def build_driver(*, headless: bool = False, offscreen: bool = False):
    """Chrome on the saved-login profile.

    headless  -- no window at all. Fine for checks and reading the inbox.
    offscreen -- a real window parked off the edge of the screen. Composing and sending use this:
                 the visible-window path is the proven one, and off-screen keeps it out of the way.
    neither   -- a normal window. Only `login` uses this, because the user must see it.
    """
    try:
        import undetected_chromedriver as uc
    except ImportError:
        raise SystemExit("Missing packages. Run:  python kit.py doctor")

    config.PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    _quiet_driver_service()
    major = chrome_major()

    driver = None
    last = None
    for attempt in range(3):        # "cannot connect to chrome" at startup is usually a one-off
        try:
            # A fresh options object per attempt: undetected-chromedriver refuses to reuse one.
            driver = uc.Chrome(options=_options(uc, headless, offscreen),
                               user_data_dir=str(config.PROFILE_DIR), version_main=major,
                               headless=headless)
            break
        except Exception as e:  # noqa: BLE001
            last = e
            msg = str(e).lower()
            if "already in use" in msg:
                raise SystemExit(
                    "The kit's Chrome profile is already open in another window or run.\n"
                    "Close that Chrome window (or wait for the other run to finish) and retry.")
            print(f"  (Chrome did not start on attempt {attempt + 1}: {str(e).splitlines()[0][:120]}; "
                  f"retrying)", flush=True)
            _kill_profile_chrome()
            import time
            time.sleep(3)
    if driver is None:
        raise SystemExit(f"Chrome would not start: {last}\nRun:  python kit.py doctor")

    # undetected-chromedriver relaunches Chrome itself, so the launch position is often ignored.
    # Moving the window after start goes through the DevTools protocol and actually takes.
    if offscreen and not headless:
        try:
            driver.set_window_rect(x=-32000, y=-32000, width=1400, height=1000)
        except Exception:
            pass
    driver.set_page_load_timeout(PAGE_TIMEOUT)
    return driver


def quit_driver(driver) -> None:
    try:
        driver.quit()
    except Exception:
        pass
