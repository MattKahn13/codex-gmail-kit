"""The guards that decide who gets emailed. None of these need a browser."""
from datetime import date, timedelta
from pathlib import Path

import pytest

from mailkit.campaign import Template, load_recipients, render_all
from mailkit.dedupe import Guard
from mailkit.followup import eligible
from mailkit.ledger import Ledger, Pacer
from mailkit.replies import classify, resolve

ROOT = Path(__file__).resolve().parent.parent


# ---- never twice -------------------------------------------------------------
def test_same_company_different_address_is_blocked():
    g = Guard.from_history([{"email": "info@acme.com", "company": "Acme"}])
    ok, why = g.check("hello@acme.com")
    assert not ok and "domain" in why


def test_company_name_normalised():
    g = Guard.from_history([{"email": "a@one.com", "company": "A&L Co."}])
    ok, why = g.check("b@two.com", company="al co")
    assert not ok and "company" in why


def test_freemail_domain_is_not_an_identity():
    g = Guard.from_history([{"email": "someone@gmail.com"}])
    assert g.check("other@gmail.com")[0]
    assert not g.check("someone@gmail.com")[0]


def test_source_url_normalised():
    g = Guard.from_history([{"email": "x@a.com", "source_url": "https://www.shop.com/"}])
    ok, why = g.check("y@b.com", source_url="http://shop.com")
    assert not ok and "source url" in why


def test_duplicate_within_one_batch():
    g = Guard()
    ok, skipped = g.filter([{"email": "a@gmail.com"}, {"email": "A@gmail.com"}])
    assert len(ok) == 1 and "this list" in skipped[0][1]


def test_bad_address_refused():
    assert not Guard().check("not-an-email")[0]


# ---- ledger, cap, bounce stop ---------------------------------------------------
def test_cap_counts_today_and_ignores_tests(tmp_path):
    led = Ledger(tmp_path / "l.jsonl")
    led.append("me@x.com", "sent", kind="test")
    led.append("a@a.com", "sent")
    led.append("b@b.com", "failed")
    p = Pacer(led, max_per_day=2)
    assert led.sent_today() == 1 and p.remaining_today() == 1
    assert led.has_passing_test()
    assert [r["email"] for r in led.contacted()] == ["a@a.com"]


def test_bounce_stop(tmp_path):
    p = Pacer(Ledger(tmp_path / "l.jsonl"), max_per_day=100, bounce_window=10)
    for s in ["sent"] * 7 + ["failed"] * 3:
        p.note(s)
    stop, why = p.should_stop()
    assert stop and "failed" in why


def test_cap_stop(tmp_path):
    led = Ledger(tmp_path / "l.jsonl")
    led.append("a@a.com", "sent")
    assert Pacer(led, max_per_day=1).should_stop()[0]


def test_delay_is_jittered(tmp_path):
    p = Pacer(Ledger(tmp_path / "l.jsonl"), mean_delay=45)
    ds = {round(p.next_delay(), 3) for _ in range(20)}
    assert len(ds) > 5 and all(27 <= d <= 63 for d in ds)


# ---- templates -----------------------------------------------------------------
def test_template_parse_and_render(tmp_path):
    t = Template.parse("Subject: Hi {company}\nAttach: f.pdf\n\nHi {first_name|there},\nbody",
                       base=tmp_path)
    assert t.attachments == [str(tmp_path / "f.pdf")]
    assert t.render({"company": "Acme", "first_name": ""}) == ("Hi Acme", "Hi there,\nbody")


def test_missing_placeholder_refuses_row():
    t = Template.parse("Subject: Hi {company}\n\nbody")
    with pytest.raises(ValueError, match="company"):
        t.render({"company": ""})


def test_template_needs_subject_and_blank_line():
    with pytest.raises(ValueError):
        Template.parse("Hello there\n\nbody")
    with pytest.raises(ValueError):
        Template.parse("Subject: x\n\n")


def test_example_campaign_renders():
    folder = ROOT / "examples" / "campaigns" / "example"
    tpl = Template.load(folder / "template.txt")
    rows = load_recipients(folder / "recipients.csv")
    emails, bad = render_all(tpl, rows)
    assert len(emails) == 3 and not bad
    assert emails[1].body.startswith("Hi there,")
    Template.load(folder / "followup.txt").render(rows[0])


def test_csv_headers_normalised(tmp_path):
    p = tmp_path / "r.csv"
    p.write_text("Email,First Name\nA@x.com,Ann\n", encoding="utf-8")
    assert load_recipients(p) == [{"email": "A@x.com", "first_name": "Ann"}]


def test_csv_without_email_column(tmp_path):
    p = tmp_path / "r.csv"
    p.write_text("name\nAnn\n", encoding="utf-8")
    with pytest.raises(ValueError, match="email"):
        load_recipients(p)


# ---- replies -------------------------------------------------------------------
def test_classify():
    assert classify("mailer-daemon@googlemail.com", "Delivery Status Notification", "")[0] == "bounced"
    assert classify("jo@acme.com", "Automatic reply: hi", "")[0] == "autoreply"
    assert classify("jo@acme.com", "Re: hi", "I am currently out of the office")[0] == "autoreply"
    assert classify("jo@acme.com", "[#12345] Re: hi", "")[0] == "autoreply"
    assert classify("jo@acme.com", "Re: hi", "Sounds good, call me Tuesday")[0] == "replied"


def _sent(email, day="2026-01-01", campaign="c", company=""):
    return {"email": email, "status": "sent", "kind": "cold", "day": day, "campaign": campaign,
            "company": company, "source_url": ""}


def test_reply_matched_by_domain_and_bounce_by_body():
    rows = [_sent("info@acme.com"), _sent("bob@gone.com"), _sent("pat@gmail.com")]
    msgs = [
        {"sender": "jane@acme.com", "subject": "Re: hi", "snippet": "yes please"},
        {"sender": "mailer-daemon@googlemail.com", "subject": "Address not found",
         "snippet": "Your message wasn't delivered to bob@gone.com because"},
        {"sender": "other@gmail.com", "subject": "Re: hi", "snippet": "hello"},
        {"sender": "news@shop.com", "subject": "Sale", "snippet": "50% off"},
    ]
    matched, unmatched = resolve(msgs, rows, own="me@me.com")
    got = {m["send"]["email"]: m["status"] for m in matched}
    assert got == {"info@acme.com": "replied", "bob@gone.com": "bounced"}
    assert len(unmatched) == 2


# ---- follow-ups ----------------------------------------------------------------
def test_followup_eligibility():
    today = date(2026, 1, 20)
    old = (today - timedelta(days=10)).isoformat()
    new = (today - timedelta(days=2)).isoformat()
    rows = [
        _sent("a@quiet.com", old),
        _sent("b@replied.com", old),
        {"email": "b@replied.com", "status": "replied", "kind": "reply", "company": ""},
        _sent("c@recent.com", new),
        _sent("d@bumped.com", old),
        {"email": "d@bumped.com", "status": "sent", "kind": "followup", "company": ""},
        _sent("e@robot.com", old),
        {"email": "e@robot.com", "status": "autoreply", "kind": "reply", "company": ""},
        _sent("f@other.com", old, campaign="different"),
        _sent("info@team.com", old),
        {"email": "info@team.com", "status": "replied", "kind": "reply", "company": ""},
    ]
    ok, skipped = eligible(rows, campaign="c", days=7, today=today)
    assert sorted(r["email"] for r in ok) == ["a@quiet.com", "e@robot.com"]
    assert len(skipped) == 4
