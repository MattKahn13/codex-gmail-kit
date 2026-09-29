"""Turn inbox messages into reply statuses on the send record.

Two mechanical jobs, done here, and one judgement job, deliberately NOT done here:

    MATCH      which email we sent does this message answer?       mechanical
    CLASSIFY   a person, an auto-responder, or a bounce?           mechanical
    LABEL      did they say yes or no?                             a human reads it

A keyword guess at "interested / not interested" would put invented outcomes into the record, so
human replies are recorded as `replied` and listed for a person (or the agent, with the person) to read.

MATCHING IS BY DOMAIN, NOT BY ADDRESS. You email info@acme.com and Jane from jane@acme.com answers.
That is the outreach working, and an address-only match would throw it away. Free-mail senders
(gmail.com and friends) only match their exact address.
"""
from __future__ import annotations

import re

from .dedupe import FREEMAIL, domain_of

BOUNCE_SENDERS = ("mailer-daemon@", "postmaster@")
BOUNCE_SUBJECTS = ("delivery status notification", "undeliverable", "returned mail",
                   "mail delivery failed", "address not found", "delivery incomplete")
AUTO_SUBJECT_PREFIX = ("automatic reply", "auto response", "auto-response", "autoreply",
                       "out of office", "automatic response", "auto:")
AUTO_BODY = (
    "this inbox is no longer monitored", "we have received your email", "we've received your message",
    "we have received your message", "thank you for contacting", "thanks for reaching out to the",
    "will respond within", "aim to respond within", "our office will be closed", "no longer supported",
    "reply above this line", "changed the status to", "i am currently out of the office",
    "i'm currently out of the office", "i am out of the office",
)
# A help desk that files your email as a ticket is not a person replying.
TICKET_RE = re.compile(r"\b[A-Z]{2,5}-\d{3,6}\b|\[#?\d{4,}\]|ticket #?\d+", re.I)
ADDR_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def classify(sender: str, subject: str, body: str) -> tuple[str, str]:
    """(status, why). status is 'bounced', 'autoreply' or 'replied'."""
    s, sub, b = sender.lower(), subject.lower(), body.lower()
    if any(x in s for x in BOUNCE_SENDERS):
        return "bounced", "sender is a mail server"
    if any(x in sub for x in BOUNCE_SUBJECTS):
        return "bounced", "delivery-failure subject"
    if any(sub.startswith(x) for x in AUTO_SUBJECT_PREFIX):
        return "autoreply", "auto-reply subject"
    if TICKET_RE.search(subject):
        return "autoreply", "help-desk ticket"
    for phrase in AUTO_BODY:
        if phrase in b:
            return "autoreply", f"auto-reply wording: {phrase!r}"
    return "replied", "a person"


def sent_index(ledger_rows: list[dict]) -> dict[str, list[dict]]:
    """domain -> [the ledger rows we sent at that domain]."""
    idx: dict[str, list[dict]] = {}
    for r in ledger_rows:
        if r.get("status") in ("sent", "unconfirmed", "scheduled") and r.get("kind") in ("cold", "followup"):
            idx.setdefault(domain_of(r["email"]), []).append(r)
    return idx


def match(sender: str, idx: dict[str, list[dict]]) -> dict | None:
    """The send this sender is answering, or None."""
    sender = sender.lower().strip("<>")
    dom = domain_of(sender)
    hits = idx.get(dom, [])
    for h in hits:
        if h["email"] == sender:
            return h
    if dom in FREEMAIL:
        return None
    return hits[0] if hits else None


def bounced_send(text: str, idx: dict[str, list[dict]], own: str) -> dict | None:
    """A bounce comes FROM a mail server; the dead address is inside the message text."""
    for cand in ADDR_RE.findall(text or ""):
        c = cand.lower().rstrip(".")
        if c == own.lower():
            continue
        for h in idx.get(domain_of(c), []):
            if h["email"] == c:
                return h
    return None


def resolve(messages: list[dict], ledger_rows: list[dict], own: str) -> tuple[list[dict], list[dict]]:
    """Match + classify inbox messages. Returns (matched, unmatched).

    matched items: {"send": <ledger row>, "status", "why", "sender", "subject", "snippet"}
    Only the strongest status per business is kept (bounced > replied > autoreply).
    """
    idx = sent_index(ledger_rows)
    rank = {"bounced": 3, "replied": 2, "autoreply": 1}
    best: dict[str, dict] = {}
    unmatched = []
    for m in messages:
        status, why = classify(m["sender"], m["subject"], m["snippet"])
        send = (bounced_send(m["subject"] + " " + m["snippet"], idx, own) if status == "bounced"
                else match(m["sender"], idx))
        if send is None:
            unmatched.append({**m, "status": status, "why": why})
            continue
        key = send["email"]
        item = {**m, "send": send, "status": status, "why": why}
        if key not in best or rank[status] > rank[best[key]["status"]]:
            best[key] = item
    return list(best.values()), unmatched


def already_recorded(ledger_rows: list[dict]) -> set[tuple[str, str]]:
    return {(r["email"], r["status"]) for r in ledger_rows if r.get("kind") == "reply"}
