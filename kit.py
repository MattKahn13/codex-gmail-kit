#!/usr/bin/env python3
"""codex-gmail-kit: send email from your own Gmail, safely, with an AI agent at the wheel.

    python kit.py doctor                       is everything installed? what's next?
    python kit.py login                        sign in to Gmail once (a Chrome window opens)
    python kit.py whoami                       which account is signed in / locked as sender
    python kit.py new <campaign>               start a campaign folder from the example
    python kit.py preview <campaign>           compose the first few in Gmail, screenshot, discard
    python kit.py test [<campaign>]            send ONE real email to yourself
    python kit.py send <campaign>              practice run; add --for-real to actually send
    python kit.py schedule <campaign> --at "2026-10-01 08:30"   Gmail's schedule-send (--for-real)
    python kit.py followup <campaign>          one nudge to the silent ones (--for-real)
    python kit.py replies                      read the inbox, record replies and bounces
    python kit.py deliverability               will it land in the inbox? SPF/DKIM/DMARC + pacing
    python kit.py status                       what has gone out, what is left today

Nothing is ever sent without --for-real (except `test`, which only emails you).
"""
from __future__ import annotations

import argparse
import importlib
import os
import platform
import random
import shutil
import sys
import time
from collections import Counter
from datetime import datetime, timedelta

if sys.version_info < (3, 9):
    sys.exit("This kit needs Python 3.9 or newer. You have " + platform.python_version())

from mailkit import config  # noqa: E402

try:
    from selenium.common.exceptions import WebDriverException
except ImportError:             # `doctor` must still run before the packages are installed
    class WebDriverException(Exception):
        pass

REAL_FLAG_HELP = "actually send. Without this flag it is a practice run and nothing leaves Gmail."


# ---------------------------------------------------------------- helpers
def _ledger():
    from mailkit.ledger import Ledger
    return Ledger(config.LEDGER_PATH)


def _need_sender(settings) -> str:
    s = settings.get("sender", "")
    if not s:
        sys.exit("No Gmail account is set up yet. Run:  python kit.py login")
    return s


def _driver(headless=False):
    """One Chrome for the whole run. KIT_HEADLESS=1 runs with no window at all: the fallback when
    a windowed Chrome will not start or keeps crashing on this computer."""
    from mailkit.browser import build_driver
    headless = headless or os.environ.get("KIT_HEADLESS") == "1"
    return build_driver(headless=headless, offscreen=not headless)


CHROME_DIED = ("Chrome closed or crashed in the middle of the run ({err}). Nothing after the last "
               "line above was sent. Close other heavy programs and retry; if it keeps happening, "
               "retry with KIT_HEADLESS=1 set.")


def _print_skips(skipped, label="Skipped"):
    if not skipped:
        return
    print(f"\n{label} ({len(skipped)}):")
    for row, why in skipped[:25]:
        print(f"  - {row.get('email') or '(no email)'}: {why}")
    if len(skipped) > 25:
        from mailkit.dedupe import summarize
        print(f"  ...and {len(skipped) - 25} more: {summarize(skipped)}")


def _plan(campaign, ledger):
    """Render + dedupe a campaign. Returns (emails, skipped)."""
    from mailkit.campaign import render_all
    from mailkit.dedupe import Guard
    tpl = campaign.template()
    for a in tpl.attachments:
        if not os.path.isfile(a):
            sys.exit(f"The template attaches '{a}', but that file does not exist.")
    rows = campaign.recipients()
    emails, bad = render_all(tpl, rows)
    guard = Guard.from_history(ledger.contacted())
    ok_rows, dup = guard.filter([e.row for e in emails])
    keep = {id(r) for r in ok_rows}
    emails = [e for e in emails if id(e.row) in keep]
    return emails, bad + dup


def _run(emails, *, send, kind, campaign_name, settings, ledger, pace=True, shots=True):
    """Compose each email in ONE shared Chrome. Records real sends in the ledger as they confirm."""
    from mailkit.compose import send_one
    from mailkit.ledger import Pacer
    from mailkit.session import open_gmail
    from mailkit.browser import quit_driver

    pacer = Pacer(ledger, max_per_day=int(settings["max_per_day"]),
                  mean_delay=float(settings["delay_seconds"]))
    shot_dir = str(config.SHOTS / campaign_name) if shots else None
    tally = Counter()
    driver = _driver()
    try:
        open_gmail(driver, settings["sender"])
        for i, e in enumerate(emails):
            if send:
                stop, why = pacer.should_stop()
                if stop:
                    print(f"\nStopping: {why}")
                    break
            res = send_one(driver, e.to, e.subject, e.body, e.attachments, send=send, shot_dir=shot_dir)
            tally[res["status"]] += 1
            shot = f"  [{res['screenshot']}]" if res.get("screenshot") else ""
            print(f"  {e.to}: {res['status']} - {res['note']}{shot}", flush=True)
            if send:
                ledger.append(e.to, res["status"], kind=kind, note=res["note"],
                              company=e.row.get("company", ""), source_url=e.row.get("source_url", ""),
                              campaign=campaign_name, subject=e.subject)
                pacer.note(res["status"])
            if i < len(emails) - 1:
                if send and pace:
                    pacer.sleep()
                else:
                    time.sleep(random.uniform(2.0, 4.0))
    except KeyboardInterrupt:
        print("\nStopped by you. Everything sent so far is recorded; a re-run picks up from here.")
    except RuntimeError as err:
        print(f"\nCould not start: {err}")
        return 1
    except WebDriverException as err:
        print("\n" + CHROME_DIED.format(err=str(err).splitlines()[0][:100]))
        return 1
    finally:
        quit_driver(driver)
    print(f"\nResult: {dict(tally) or 'nothing composed'}")
    if tally.get("unconfirmed"):
        print("'unconfirmed' means Send was clicked but Gmail never showed 'Message sent'. Look in the")
        print("Sent folder. They are recorded as contacted so they will not be emailed again.")
    return 0 if not tally.get("failed") else 1


# ---------------------------------------------------------------- commands
def cmd_doctor(_a) -> int:
    ok = True
    print(f"Python {platform.python_version()} on {platform.system()}  OK")
    for mod, pip in (("selenium", "selenium"), ("undetected_chromedriver", "undetected-chromedriver"),
                     ("dns", "dnspython")):
        try:
            importlib.import_module(mod)
            print(f"package {pip}  OK")
        except Exception:
            ok = False
            print(f"package {pip}  MISSING")
    if not ok:
        print("\nInstall the packages with:\n  python -m pip install -r requirements.txt")
    from mailkit.browser import chrome_major, chrome_path
    path = chrome_path()
    if path:
        print(f"Google Chrome {chrome_major() or '(version unknown)'}  OK")
    else:
        ok = False
        print("Google Chrome  MISSING - install it from https://www.google.com/chrome/")
    settings = config.load_settings()
    sender = settings.get("sender")
    print(f"Gmail login  {'saved for ' + sender if sender else 'NOT SET UP'}")
    print(f"Daily limit  {settings['max_per_day']} emails")
    if not ok:
        print("\nNEXT: fix the MISSING items above, then run `python kit.py doctor` again.")
        return 1
    if not sender:
        print("\nNEXT: python kit.py login")
    elif not _ledger().has_passing_test():
        print("\nNEXT: make a campaign (python kit.py new <name>), preview it, then python kit.py test")
    else:
        print("\nAll set.")
    return 0


def cmd_login(a) -> int:
    from mailkit.session import login
    return login(switch=a.switch)


def cmd_whoami(_a) -> int:
    from mailkit.session import whoami
    return whoami()


def cmd_new(a) -> int:
    src = config.ROOT / "examples" / "campaigns" / "example"
    dst = config.CAMPAIGNS / a.campaign
    if dst.exists():
        sys.exit(f"my/campaigns/{a.campaign} already exists. Pick another name or edit that one.")
    shutil.copytree(src, dst)
    print(f"Created my/campaigns/{a.campaign}/ with an example template.txt and recipients.csv.")
    print("Edit both, then run:  python kit.py preview " + a.campaign)
    return 0


def cmd_preview(a) -> int:
    from mailkit.campaign import Campaign
    settings = config.load_settings()
    _need_sender(settings)
    ledger = _ledger()
    camp = Campaign.open(a.campaign)
    emails, skipped = _plan(camp, ledger)
    print(f"Campaign '{a.campaign}': {len(emails)} ready to send, {len(skipped)} skipped.")
    _print_skips(skipped)
    batch = emails[:a.limit]
    if not batch:
        print("\nNothing to preview.")
        return 0
    print(f"\nPRACTICE: composing the first {len(batch)} in Gmail, then discarding. Nothing is sent.\n")
    for e in batch:
        print(f"--- to {e.to}\nSubject: {e.subject}\n\n{e.body}\n"
              + (f"[attached: {', '.join(e.attachments)}]\n" if e.attachments else ""))
    return _run(batch, send=False, kind="cold", campaign_name=a.campaign, settings=settings,
                ledger=ledger)


def cmd_test(a) -> int:
    from mailkit.campaign import Campaign, Email
    settings = config.load_settings()
    sender = _need_sender(settings)
    ledger = _ledger()
    if a.campaign:
        camp = Campaign.open(a.campaign)
        emails, _ = _plan(camp, ledger)
        if not emails:
            sys.exit("That campaign has no sendable rows to base a test on.")
        e = emails[0]
        test = Email({}, sender, "[TEST] " + e.subject, e.body, e.attachments)
        name = a.campaign
    else:
        test = Email({}, sender, "[TEST] codex-gmail-kit is working",
                     "This is a test email sent by codex-gmail-kit to check that sending works.", [])
        name = "_test"
    print(f"Sending ONE real test email to yourself ({sender}).")
    from mailkit.compose import send_one
    from mailkit.session import open_gmail
    from mailkit.browser import quit_driver
    driver = _driver()
    try:
        open_gmail(driver, sender)
        res = send_one(driver, test.to, test.subject, test.body, test.attachments, send=True,
                       shot_dir=str(config.SHOTS / name))
    except RuntimeError as err:
        print(f"Could not start: {err}")
        return 1
    except WebDriverException as err:
        print(CHROME_DIED.format(err=str(err).splitlines()[0][:100]))
        return 1
    finally:
        quit_driver(driver)
    ledger.append(sender, res["status"], kind="test", note=res["note"], campaign=name,
                  subject=test.subject)
    print(f"  {sender}: {res['status']} - {res['note']}")
    if res["status"] == "sent":
        print("\nCheck your inbox for it. Real sends are now unlocked (they still need --for-real).")
        return 0
    return 1


def cmd_send(a) -> int:
    from mailkit.campaign import Campaign
    settings = config.load_settings()
    _need_sender(settings)
    ledger = _ledger()
    camp = Campaign.open(a.campaign)
    emails, skipped = _plan(camp, ledger)
    _print_skips(skipped)
    remaining = max(0, int(settings["max_per_day"]) - ledger.sent_today())
    limit = min(a.limit or remaining, remaining) if a.for_real else (a.limit or 3)
    batch = emails[:limit]
    print(f"\nCampaign '{a.campaign}': {len(emails)} ready, {len(skipped)} skipped, "
          f"{ledger.sent_today()}/{settings['max_per_day']} already sent today.")
    if not a.for_real:
        print(f"PRACTICE RUN: composing {len(batch)} and discarding them. Add --for-real to send.\n")
        return _run(batch, send=False, kind="cold", campaign_name=a.campaign, settings=settings,
                    ledger=ledger)
    if not ledger.has_passing_test():
        sys.exit("Real sends are locked until a test email to yourself works. Run:  python kit.py test")
    if not batch:
        print("Nothing to send (daily limit reached, or everyone on the list is already contacted).")
        return 0
    mins = len(batch) * float(settings["delay_seconds"]) / 60
    print(f"SENDING {len(batch)} real emails now, about {mins:.0f} minutes with gaps between them.\n")
    return _run(batch, send=True, kind="cold", campaign_name=a.campaign, settings=settings,
                ledger=ledger)


def cmd_schedule(a) -> int:
    from mailkit.campaign import Campaign
    from mailkit.schedule import schedule_one
    from mailkit.session import open_gmail
    from mailkit.browser import quit_driver
    settings = config.load_settings()
    sender = _need_sender(settings)
    ledger = _ledger()
    try:
        start = datetime.strptime(a.at, "%Y-%m-%d %H:%M")
    except ValueError:
        sys.exit('Give the time as --at "YYYY-MM-DD HH:MM", 24-hour, your local time.')
    camp = Campaign.open(a.campaign)
    emails, skipped = _plan(camp, ledger)
    _print_skips(skipped)
    remaining = max(0, int(settings["max_per_day"]) - ledger.sent_today())
    batch = emails[:min(a.limit or remaining, remaining)] if a.for_real else emails[:a.limit or 3]
    if a.for_real and not ledger.has_passing_test():
        sys.exit("Real sends are locked until a test email to yourself works. Run:  python kit.py test")
    print(f"{'SCHEDULING' if a.for_real else 'PRACTICE (nothing scheduled)'}: {len(batch)} emails, "
          f"first at {start:%Y-%m-%d %H:%M}, then every {a.every} min.\n")
    driver = _driver()
    tally = Counter()
    try:
        open_gmail(driver, sender)
        for i, e in enumerate(batch):
            when = start + timedelta(minutes=a.every * i)
            res = schedule_one(driver, e.to, e.subject, e.body, when, e.attachments,
                               for_real=a.for_real, shot_dir=str(config.SHOTS / a.campaign))
            tally[res["status"]] += 1
            print(f"  {e.to} @ {when:%m-%d %H:%M}: {res['status']} - {res['note']}", flush=True)
            if a.for_real:
                ledger.append(e.to, res["status"], kind="cold", note=res["note"],
                              company=e.row.get("company", ""), source_url=e.row.get("source_url", ""),
                              campaign=a.campaign, subject=e.subject)
    except RuntimeError as err:
        print(f"Could not start: {err}")
        return 1
    except WebDriverException as err:
        print(CHROME_DIED.format(err=str(err).splitlines()[0][:100]))
        return 1
    except KeyboardInterrupt:
        print("\nStopped by you. Everything scheduled so far is recorded.")
    finally:
        quit_driver(driver)
    print(f"\nResult: {dict(tally)}. Scheduled emails sit in Gmail's 'Scheduled' folder until they go.")
    return 0 if not tally.get("failed") else 1


def cmd_followup(a) -> int:
    from mailkit.campaign import Campaign, Email
    from mailkit.followup import eligible
    settings = config.load_settings()
    _need_sender(settings)
    ledger = _ledger()
    camp = Campaign.open(a.campaign)
    days = a.days if a.days is not None else int(settings["followup_after_days"])
    tpl = camp.followup_template()
    by_email = {}
    try:
        by_email = {r["email"].lower(): r for r in camp.recipients()}
    except (FileNotFoundError, ValueError):
        pass
    rows, skipped = eligible(ledger.rows(), campaign=a.campaign, days=days)
    emails = []
    for r in rows:
        data = by_email.get(r["email"], {"email": r["email"], "company": r.get("company", "")})
        try:
            subject, body = tpl.render(data)
        except ValueError as err:
            skipped.append((r, str(err)))
            continue
        emails.append(Email({**data, "company": r.get("company", ""),
                             "source_url": r.get("source_url", "")}, r["email"], subject, body,
                            list(tpl.attachments)))
    print(f"Follow-ups for '{a.campaign}' (silent {days}+ days): {len(emails)} eligible.")
    print("Tip: run `python kit.py replies` first, so anyone who answered is excluded.")
    _print_skips(skipped, "Not eligible")
    remaining = max(0, int(settings["max_per_day"]) - ledger.sent_today())
    if not a.for_real:
        batch = emails[:a.limit or 3]
        print(f"\nPRACTICE RUN: composing {len(batch)} and discarding. Add --for-real to send.\n")
        return _run(batch, send=False, kind="followup", campaign_name=a.campaign, settings=settings,
                    ledger=ledger)
    if not ledger.has_passing_test():
        sys.exit("Real sends are locked until a test email to yourself works. Run:  python kit.py test")
    batch = emails[:min(a.limit or remaining, remaining)]
    if not batch:
        print("Nothing to send.")
        return 0
    print(f"\nSENDING {len(batch)} follow-ups.\n")
    return _run(batch, send=True, kind="followup", campaign_name=a.campaign, settings=settings,
                ledger=ledger)


def cmd_replies(a) -> int:
    from mailkit.inbox import search
    from mailkit.replies import already_recorded, resolve
    from mailkit.session import open_gmail
    from mailkit.browser import quit_driver
    settings = config.load_settings()
    sender = _need_sender(settings)
    ledger = _ledger()
    rows = ledger.rows()
    driver = _driver(headless=True)
    try:
        open_gmail(driver, sender)
        msgs = search(driver, f"in:inbox -from:me newer_than:{a.days}d", own=sender)
        msgs += search(driver, f"(from:mailer-daemon OR from:postmaster) newer_than:{a.days}d",
                       own=sender)
    except RuntimeError as err:
        print(f"Could not start: {err}")
        return 1
    except WebDriverException as err:
        print(CHROME_DIED.format(err=str(err).splitlines()[0][:100]))
        return 1
    finally:
        quit_driver(driver)
    matched, unmatched = resolve(msgs, rows, sender)
    done = already_recorded(rows)
    new = 0
    for m in matched:
        s = m["send"]
        if (s["email"], m["status"]) in done:
            continue
        ledger.append(s["email"], m["status"], kind="reply", note=f"{m['sender']}: {m['subject']}",
                      company=s.get("company", ""), source_url=s.get("source_url", ""),
                      campaign=s.get("campaign", ""), subject=s.get("subject", ""))
        new += 1
    print(f"Read {len(msgs)} inbox conversations from the last {a.days} days. "
          f"{len(matched)} answer something you sent; {new} new, recorded in my/ledger.jsonl.\n")
    for status in ("replied", "bounced", "autoreply"):
        group = [m for m in matched if m["status"] == status]
        if group:
            print({"replied": "PEOPLE WHO REPLIED (read these yourself):",
                   "bounced": "BOUNCED (address is dead; will not be followed up):",
                   "autoreply": "AUTO-REPLIES (a robot answered; still eligible for a follow-up):"}[status])
            for m in group:
                print(f"  {m['send']['email']}  <- {m['sender']}: {m['subject'][:70]} | {m['snippet'][:90]}")
            print()
    return 0


def cmd_deliverability(_a) -> int:
    from mailkit.deliverability import report
    return report(config.load_settings().get("sender", ""))


def cmd_status(_a) -> int:
    settings = config.load_settings()
    ledger = _ledger()
    rows = ledger.rows()
    print(f"Sender: {settings.get('sender') or '(not set up: python kit.py login)'}")
    print(f"Today: {ledger.sent_today()}/{settings['max_per_day']} sent")
    print(f"Test email to yourself: {'passed' if ledger.has_passing_test() else 'not yet (python kit.py test)'}")
    camps = Counter((r.get("campaign") or "-", r.get("kind"), r.get("status")) for r in rows
                    if r.get("kind") != "test")
    if camps:
        print("\nBy campaign:")
        for (c, k, s), n in sorted(camps.items()):
            print(f"  {c:20} {k:9} {s:12} {n}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="kit.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor").set_defaults(fn=cmd_doctor)
    p = sub.add_parser("login")
    p.add_argument("--switch", action="store_true", help="make a different account the sender")
    p.set_defaults(fn=cmd_login)
    sub.add_parser("whoami").set_defaults(fn=cmd_whoami)
    p = sub.add_parser("new")
    p.add_argument("campaign")
    p.set_defaults(fn=cmd_new)
    p = sub.add_parser("preview")
    p.add_argument("campaign")
    p.add_argument("--limit", type=int, default=3)
    p.set_defaults(fn=cmd_preview)
    p = sub.add_parser("test")
    p.add_argument("campaign", nargs="?")
    p.set_defaults(fn=cmd_test)
    p = sub.add_parser("send")
    p.add_argument("campaign")
    p.add_argument("--limit", type=int, default=0, help="at most this many (default: today's allowance)")
    p.add_argument("--for-real", action="store_true", help=REAL_FLAG_HELP)
    p.set_defaults(fn=cmd_send)
    p = sub.add_parser("schedule")
    p.add_argument("campaign")
    p.add_argument("--at", required=True, help='first send time, "YYYY-MM-DD HH:MM" local, 24-hour')
    p.add_argument("--every", type=int, default=4, help="minutes between scheduled emails")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--for-real", action="store_true", help=REAL_FLAG_HELP)
    p.set_defaults(fn=cmd_schedule)
    p = sub.add_parser("followup")
    p.add_argument("campaign")
    p.add_argument("--days", type=int, default=None)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--for-real", action="store_true", help=REAL_FLAG_HELP)
    p.set_defaults(fn=cmd_followup)
    p = sub.add_parser("replies")
    p.add_argument("--days", type=int, default=30)
    p.set_defaults(fn=cmd_replies)
    sub.add_parser("deliverability").set_defaults(fn=cmd_deliverability)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    a = ap.parse_args()
    return a.fn(a) or 0


if __name__ == "__main__":
    raise SystemExit(main())
