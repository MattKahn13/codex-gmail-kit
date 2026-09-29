"""Where everything lives, and the user's settings.

Everything personal is under `my/` (git-ignored) and `.chrome-profile/` (git-ignored), so nothing the
user does can be pushed to GitHub by accident.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MY = ROOT / "my"
CAMPAIGNS = MY / "campaigns"
LEDGER_PATH = MY / "ledger.jsonl"
SETTINGS_PATH = MY / "settings.json"
SHOTS = MY / "screenshots"

# The saved Gmail login. One folder holds ONE signed-in Google account. To send from a second
# address, point KIT_PROFILE_DIR at a different folder and sign in there once.
PROFILE_DIR = Path(os.environ.get("KIT_PROFILE_DIR", ROOT / ".chrome-profile"))

GMAIL_URL = "https://mail.google.com/mail/u/0/"

IS_MAC = sys.platform == "darwin"
IS_WINDOWS = os.name == "nt"

DEFAULTS = {
    # The address this kit is allowed to send from. Filled in by `kit.py login`.
    "sender": "",
    # A new or cold address should start low and climb slowly. See `kit.py deliverability`.
    "max_per_day": 20,
    # Average seconds between emails. Each gap is randomised +/- 40% so it does not look scripted.
    "delay_seconds": 45,
    # Follow-ups go only to people who have been silent at least this many days.
    "followup_after_days": 7,
}


def load_settings() -> dict:
    s = dict(DEFAULTS)
    if SETTINGS_PATH.exists():
        try:
            s.update(json.loads(SETTINGS_PATH.read_text(encoding="utf-8")))
        except json.JSONDecodeError as e:
            raise SystemExit(f"my/settings.json is not valid JSON ({e}). Fix it or delete it.")
    return s


def save_settings(s: dict) -> None:
    MY.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(s, indent=2) + "\n", encoding="utf-8")
