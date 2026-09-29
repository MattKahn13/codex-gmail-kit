"""The send record: one line per event, appended, never rewritten.

`my/ledger.jsonl` is the single source of truth for who has been contacted. A recipient list gets
edited and regenerated; the ledger does not. Each line is a JSON object:

    {"email", "domain", "company", "source_url", "campaign", "subject", "kind",
     "status", "note", "at", "day"}

kind    "cold" (first email), "followup", "test", "reply" (a status learned from the inbox)
status  "sent", "failed", "unconfirmed", "scheduled", "replied", "autoreply", "bounced"

A row is written the moment Gmail confirms a send, so a crash in the middle of a batch loses nothing
and a re-run picks up where it stopped.
"""
from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from .dedupe import domain_of

CONTACTED = ("sent", "scheduled", "unconfirmed")   # counts as "we reached out"
ENDED = ("replied", "bounced")                      # the conversation is over for follow-ups


def _today() -> str:
    return date.today().isoformat()


@dataclass
class Ledger:
    path: Path

    def append(self, email: str, status: str, *, kind: str = "cold", note: str = "",
               company: str = "", source_url: str = "", campaign: str = "", subject: str = "") -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "email": email.strip().lower(), "domain": domain_of(email), "company": company,
            "source_url": source_url, "campaign": campaign, "subject": subject, "kind": kind,
            "status": status, "note": note[:300],
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "day": _today(),
        }
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")

    def rows(self) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

    def contacted(self) -> list[dict]:
        """Every business we reached out to, for the never-twice guard. Tests to yourself excluded."""
        return [r for r in self.rows() if r.get("status") in CONTACTED and r.get("kind") != "test"]

    def sent_today(self) -> int:
        t = _today()
        return sum(1 for r in self.rows()
                   if r.get("status") in ("sent", "unconfirmed") and r.get("day") == t
                   and r.get("kind") != "test")

    def has_passing_test(self) -> bool:
        return any(r.get("kind") == "test" and r.get("status") == "sent" for r in self.rows())


@dataclass
class Pacer:
    """How many may go today, how long to wait between, and when to stop early.

    The cap is on the DAY, counted from the ledger, so a second run the same day only tops up to it.
    The bounce stop exists because a bad list is a reputation problem: pushing through a run of
    failures is how a Gmail account gets flagged.
    """
    ledger: Ledger
    max_per_day: int = 20
    mean_delay: float = 45.0
    jitter: float = 0.4
    bounce_window: int = 25
    bounce_threshold: float = 0.30
    recent: list = field(default_factory=list)

    def remaining_today(self) -> int:
        return max(0, self.max_per_day - self.ledger.sent_today())

    def next_delay(self) -> float:
        lo, hi = self.mean_delay * (1 - self.jitter), self.mean_delay * (1 + self.jitter)
        return max(5.0, random.uniform(lo, hi))

    def note(self, status: str) -> None:
        self.recent.append(status)
        if len(self.recent) > self.bounce_window:
            self.recent.pop(0)

    def should_stop(self) -> tuple[bool, str]:
        if self.remaining_today() <= 0:
            return True, f"daily limit reached ({self.max_per_day} today)"
        if len(self.recent) >= self.bounce_window:
            bad = sum(1 for s in self.recent if s in ("bounced", "failed"))
            rate = bad / len(self.recent)
            if rate >= self.bounce_threshold:
                return True, (f"{rate:.0%} of the last {self.bounce_window} attempts failed. Stopping "
                              f"to protect the account. Check the list for bad addresses.")
        return False, ""

    def sleep(self) -> None:
        time.sleep(self.next_delay())
