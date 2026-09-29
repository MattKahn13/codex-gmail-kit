"""Read the inbox through the same saved login. No app password, no Google Cloud project.

READ-ONLY by construction: it runs a Gmail search and reads the result rows. It never opens, replies
to, archives or marks anything.
"""
from __future__ import annotations

import time
from urllib.parse import quote_plus

# tr.zA is a conversation row; span[email] carries each participant's address;
# .y6 is the subject; .y2 the snippet (the first line of the latest message).
ROWS_JS = r"""
return [...document.querySelectorAll('tr.zA')].slice(0, arguments[0]).map(tr => {
  const emails = [...tr.querySelectorAll('span[email]')].map(s => (s.getAttribute('email') || '').toLowerCase());
  const subj = tr.querySelector('.y6 span, .bog');
  const snip = tr.querySelector('.y2');
  return {emails, subject: subj ? subj.textContent : '', snippet: snip ? snip.textContent : ''};
});
"""


def search(driver, query: str, *, own: str, limit: int = 100, pages: int = 4,
           settle: float = 3.0) -> list[dict]:
    """Rows matching a Gmail search, as {sender, subject, snippet}.

    `sender` is the first participant that is not you. Scrolls for more than one screen of results
    and de-duplicates, because a scroll re-reads rows already seen.
    """
    driver.get("https://mail.google.com/mail/u/0/#search/" + quote_plus(query))
    time.sleep(settle)
    seen, out = set(), []
    for i in range(max(1, pages)):
        if i:
            try:
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            except Exception:
                break
            time.sleep(settle)
        try:
            rows = driver.execute_script(ROWS_JS, limit) or []
        except Exception:
            rows = []
        new = 0
        for r in rows:
            others = [e for e in r.get("emails", []) if e and e != own.lower()]
            sender = others[0] if others else ""
            if not sender:
                continue
            item = {"sender": sender, "subject": (r.get("subject") or "").strip(),
                    "snippet": (r.get("snippet") or "").strip()}
            k = (item["sender"], item["subject"], item["snippet"][:60])
            if k not in seen:
                seen.add(k)
                out.append(item)
                new += 1
        if new == 0:
            break
    return out
