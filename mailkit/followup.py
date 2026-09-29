"""Who gets the ONE follow-up.

The never-twice guard exists to stop a second email. A follow-up IS a second email, so it cannot
reuse that guard; it carries its own, stricter rules:

  * the address must already have a confirmed cold send in the ledger (nothing new is cold-mailed)
  * at least N days have passed since that send
  * the business has NOT replied or bounced, from ANY address at its domain (run `kit.py replies` first)
  * the business has never had a follow-up. One follow-up is a nudge; two is a sequence.

An auto-reply ("I'm out of the office") does NOT exclude: the business itself never answered.
"""
from __future__ import annotations

from datetime import date, timedelta

from .dedupe import Guard
from .ledger import ENDED


def eligible(ledger_rows: list[dict], *, campaign: str, days: int,
             today: date | None = None) -> tuple[list[dict], list[tuple[dict, str]]]:
    """(rows to follow up, [(row, why_not)]) for one campaign."""
    today = today or date.today()
    cutoff = (today - timedelta(days=days)).isoformat()

    stop_rows = [r for r in ledger_rows
                 if r.get("status") in ENDED
                 or (r.get("kind") == "followup" and r.get("status") in ("sent", "unconfirmed", "scheduled"))]
    guard = Guard.from_history(stop_rows)

    firsts: dict[str, dict] = {}
    for r in ledger_rows:
        if r.get("kind") == "cold" and r.get("status") == "sent" and r.get("campaign") == campaign:
            firsts.setdefault(r["email"], r)

    ok, skipped = [], []
    for r in firsts.values():
        if r.get("day", "") > cutoff:
            skipped.append((r, f"sent {r.get('day')}, less than {days} days ago"))
            continue
        allowed, why = guard.check(r["email"], r.get("company", ""), r.get("source_url", ""))
        if not allowed:
            why = why.replace("already contacted", "replied, bounced or already followed up")
            skipped.append((r, why))
            continue
        guard.accept(r["email"], r.get("company", ""), r.get("source_url", ""))
        ok.append(r)
    return ok, skipped
