"""Never contact the same business twice, however many addresses it has.

Deduping on the email address alone is not enough. The same company turns up as `info@` in one list
and `hello@` in the next, and an address-only check mails it twice. So four keys are checked, and a
row is dropped if ANY of them has been contacted before:

    email       exact, lowercased
    domain      the part after @, EXCEPT free-mail (gmail.com is millions of unrelated people)
    source_url  the website / listing / profile the lead came from, if the list has it
    company     the company name, lowercased with punctuation removed ("A&L Co." == "al co")

Every key is checked twice: against the send history AND against the rows already accepted in the
current batch. Two rows in one list can point at the same business.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

FREEMAIL = {"gmail.com", "googlemail.com", "yahoo.com", "outlook.com", "hotmail.com", "aol.com",
            "icloud.com", "me.com", "mac.com", "live.com", "msn.com", "protonmail.com", "proton.me",
            "gmx.com", "yandex.com", "mail.com", "zoho.com", "comcast.net", "att.net",
            "verizon.net", "sbcglobal.net", "ymail.com"}

KEYS = ("email", "domain", "source_url", "company")


def norm_company(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def domain_of(email: str) -> str:
    return (email or "").rpartition("@")[2].strip().lower()


def norm_url(url: str) -> str:
    u = (url or "").strip().lower()
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"^www\.", "", u)
    return u.rstrip("/")


def identity_keys(email: str = "", company: str = "", source_url: str = "") -> dict:
    dom = domain_of(email)
    return {
        "email": (email or "").strip().lower(),
        "domain": "" if dom in FREEMAIL else dom,
        "source_url": norm_url(source_url),
        "company": norm_company(company),
    }


@dataclass
class Guard:
    seen: dict = field(default_factory=lambda: {k: set() for k in KEYS})
    batch: dict = field(default_factory=lambda: {k: set() for k in KEYS})

    @classmethod
    def from_history(cls, rows) -> "Guard":
        """`rows` are dicts with any of email / company / source_url."""
        g = cls()
        for r in rows:
            for k, v in identity_keys(r.get("email", ""), r.get("company", ""),
                                      r.get("source_url", "")).items():
                if v:
                    g.seen[k].add(v)
        return g

    def check(self, email: str, company: str = "", source_url: str = "") -> tuple[bool, str]:
        """(allowed, reason). The reason says which key refused it."""
        keys = identity_keys(email, company, source_url)
        if not keys["email"] or "@" not in keys["email"]:
            return False, "no valid email address"
        for k in KEYS:
            v = keys[k]
            if not v:
                continue
            if v in self.seen[k]:
                return False, f"already contacted (same {k.replace('_', ' ')})"
            if v in self.batch[k]:
                return False, f"duplicate in this list (same {k.replace('_', ' ')})"
        return True, ""

    def accept(self, email: str, company: str = "", source_url: str = "") -> None:
        for k, v in identity_keys(email, company, source_url).items():
            if v:
                self.batch[k].add(v)

    def filter(self, rows: list[dict]) -> tuple[list[dict], list[tuple[dict, str]]]:
        """Split rows into (allowed, [(row, why_skipped)])."""
        ok, skipped = [], []
        for r in rows:
            allowed, why = self.check(r.get("email", ""), r.get("company", ""), r.get("source_url", ""))
            if allowed:
                self.accept(r.get("email", ""), r.get("company", ""), r.get("source_url", ""))
                ok.append(r)
            else:
                skipped.append((r, why))
        return ok, skipped


def summarize(skipped) -> dict:
    return dict(Counter(why for _, why in skipped))
