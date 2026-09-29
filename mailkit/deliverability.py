"""Will these emails land in the inbox, or in spam?

Sending working (Gmail says "sent") is not the same as landing in the inbox. Two things decide most of
it, and both are cheap to check before a campaign:

1. AUTHENTICATION. Since February 2024, Gmail and Yahoo expect senders on their own domain to publish
   SPF, DKIM and DMARC records. Missing any of them means throttling or the spam folder.
2. VOLUME. A new or quiet address cannot suddenly send hundreds of cold emails. Start around 5-20 a
   day and climb slowly over weeks. Keep spam complaints under 0.3%, which mostly means only emailing
   people with a real reason to hear from you.
"""
from __future__ import annotations

from .dedupe import FREEMAIL, domain_of

WARMUP = """Warm-up pace (what deliverability services broadly agree on):
  weeks 1-2   5-20 a day
  after that  add a few a day, week by week
  ceiling     ~40-50 cold emails a day from one mailbox is where scrutiny starts
Set your daily limit in my/settings.json ("max_per_day"). The kit enforces it."""


def _txt(name: str) -> list[str]:
    import dns.resolver
    try:
        ans = dns.resolver.resolve(name, "TXT", lifetime=8)
    except Exception:
        return []
    out = []
    for r in ans:
        out.append(b"".join(r.strings).decode("utf-8", "replace"))
    return out


def _mx(name: str) -> list[str]:
    import dns.resolver
    try:
        return [str(r.exchange).lower().rstrip(".") for r in dns.resolver.resolve(name, "MX", lifetime=8)]
    except Exception:
        return []


def check(sender: str) -> list[tuple[str, bool, str]]:
    """[(name, ok, advice)] for the sender's domain. Empty list for a free-mail address."""
    dom = domain_of(sender)
    if not dom or dom in FREEMAIL:
        return []
    results = []
    spf = [t for t in _txt(dom) if t.lower().startswith("v=spf1")]
    results.append(("SPF", bool(spf),
                    spf[0] if spf else f"Add a TXT record on {dom}:  v=spf1 include:_spf.google.com ~all"))
    dmarc = [t for t in _txt(f"_dmarc.{dom}") if t.lower().startswith("v=dmarc1")]
    results.append(("DMARC", bool(dmarc),
                    dmarc[0] if dmarc else
                    f"Add a TXT record on _dmarc.{dom}:  v=DMARC1; p=none; rua=mailto:{sender}"))
    google = any("google" in m for m in _mx(dom))
    dkim = [t for t in _txt(f"google._domainkey.{dom}") if "dkim" in t.lower() or "p=" in t]
    if google:
        results.append(("DKIM", bool(dkim),
                        "found (google selector)" if dkim else
                        "Turn on DKIM: Google Admin console > Apps > Google Workspace > Gmail > "
                        "Authenticate email > Generate new record, add it to DNS, then Start authentication."))
    else:
        results.append(("DKIM", bool(dkim),
                        "found" if dkim else
                        "Could not find a Google DKIM record. If this domain's mail is not on Google "
                        "Workspace, check DKIM with your mail provider."))
    return results


def report(sender: str) -> int:
    if not sender:
        print("No sender set yet. Run:  python kit.py login")
        return 1
    dom = domain_of(sender)
    print(f"Sender: {sender}\n")
    if dom in FREEMAIL:
        print(f"{dom} is a free-mail address, so its SPF/DKIM/DMARC are Google's and already fine.")
        print("What matters for you is volume and content: a personal Gmail account sending lots of")
        print("cold email to strangers is the fastest way to get it limited. For business outreach,")
        print("a Google Workspace address on your own domain is safer.\n")
        print(WARMUP)
        return 0
    results = check(sender)
    bad = 0
    for name, ok, advice in results:
        print(f"  {'OK     ' if ok else 'MISSING'} {name}: {advice}")
        bad += not ok
    print()
    if bad:
        print("Fix the MISSING records in your domain's DNS settings (where you bought the domain),")
        print("then run this again. DNS changes can take up to an hour to show up.\n")
    else:
        print("All three are in place.\n")
    print(WARMUP)
    return 1 if bad else 0
