"""Compose, verify and send ONE email in Gmail's web page.

Every step here is a response to something that actually went wrong. The short version of the flow:

    close_stale_composes      a window left open by a crashed run is addressed to someone ELSE
    open_compose_url          compose by URL: no click to register, full screen, to+subject prefilled
    recipient_present         the address must show as a committed chip
    our window, by subject    never "the first compose window"; that one may be stale
    insert_body               typed ABOVE the signature, so the signature survives
    body_ok                   the message text is really there, and above the signature
    attach                    uploads must finish before Send, or the mail leaves without them
    send + confirm_sent       confirmed by Gmail, never assumed from "we clicked"

Read AGENTS.md's "Gmail traps" before changing any of it.
"""
from __future__ import annotations

import os
import re
import time
from typing import Optional, Sequence
from urllib.parse import urlencode

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import WebDriverException

from . import config

COMPOSE_TIMEOUT = 30
UPLOAD_TIMEOUT = 180
MOD = Keys.COMMAND if config.IS_MAC else Keys.CONTROL


def _sized(el) -> bool:
    try:
        r = el.rect
        return r["width"] > 40 and r["height"] > 8
    except WebDriverException:
        return False


def open_composes(driver) -> list:
    """Every compose window that is open AND usable.

    `input[name=subjectbox]` is unique to compose. `div[role=dialog]` is NOT proof of anything: the
    full-screen compose used here is not a dialog at all, and the account menu is one.
    A compose can also be restored minimised, present in the page at zero size, so size is required.
    """
    return [e for e in driver.find_elements(By.CSS_SELECTOR, "input[name='subjectbox']") if _sized(e)]


def close_stale_composes(driver, timeout: int = 12) -> int:
    """Close compose windows this run did not open.

    Gmail restores a compose that was open when a previous session died. It carries the OLD subject
    and an already-committed recipient. Adopting it means sending the wrong message to the wrong
    person, so every message starts from a clean slate.
    """
    closed = 0
    end = time.time() + timeout
    while open_composes(driver) and time.time() < end:
        hit = False
        for el in driver.find_elements(
                By.CSS_SELECTOR,
                "img[aria-label*='Save & close'], div[aria-label*='Save & close'], "
                "img[aria-label*='Close'], div[role='button'][aria-label*='Close']"):
            try:
                if el.is_displayed():
                    driver.execute_script("arguments[0].click();", el)
                    hit, closed = True, closed + 1
                    time.sleep(0.5)
                    break
            except WebDriverException:
                continue
        if not hit:
            try:
                driver.switch_to.active_element.send_keys(Keys.ESCAPE)
                time.sleep(0.5)
            except Exception:
                break
        accept_alert(driver)
    return closed


def accept_alert(driver) -> bool:
    """Answer a stray "Discard your message?" browser alert. Left open, it breaks the NEXT email."""
    try:
        driver.switch_to.alert.accept()
        time.sleep(0.3)
        return True
    except Exception:
        return False


def open_compose_url(driver, to: str, subject: str) -> None:
    """Open a fresh full-screen compose with To and Subject already filled in.

    NO `body` parameter, on purpose: `&body=` replaces the whole body, which wipes the signature Gmail
    put there. The body is typed in afterwards by `insert_body`.

    The recipient arrives as a committed chip, so there is no empty To box to find afterwards. That is
    correct. Check the chip, not an input.
    """
    q = urlencode({"view": "cm", "fs": "1", "tf": "1", "to": to, "su": subject})
    driver.get(f"https://mail.google.com/mail/u/0/?{q}")
    end = time.time() + COMPOSE_TIMEOUT
    while time.time() < end:
        accept_alert(driver)
        if open_composes(driver):
            time.sleep(1.0)
            return
        time.sleep(0.4)
    raise RuntimeError("the compose window did not open")


_INSERT_BODY_JS = r"""
const content = arguments[0];
const el = [...document.querySelectorAll("div[aria-label='Message Body'], div[g_editable='true']")]
  .find(e => e.offsetWidth > 0 && e.offsetHeight > 0);
if (!el) return "no body element";
el.focus();
// Caret to the very start, ABOVE the signature. Clicking the body drops it after the signature.
const r = document.createRange();
r.setStart(el, 0);
r.collapse(true);
const s = window.getSelection();
s.removeAllRanges();
s.addRange(r);
// Selenium's send_keys lands nowhere in this editor and still reports success. insertText counts
// as a real user edit. One trailing newline: Gmail already keeps a blank line above the signature.
document.execCommand("insertText", false, content + "\n");
return "ok";
"""


def insert_body(driver, content: str) -> None:
    """Type the message above the account signature, leaving the signature intact.

    Never clear the body first. The signature is the sign-off (and, for business email, where the
    postal address and unsubscribe line usually live).
    """
    end = time.time() + 12
    out = None
    while time.time() < end:          # the body can render a beat after the compose opens
        out = driver.execute_script(_INSERT_BODY_JS, content)
        if out == "ok":
            time.sleep(0.6)
            return
        if out == "no body element":
            time.sleep(0.5)
            continue
        break
    raise RuntimeError(f"could not type the message: {out}")


_BODY_CHECK_JS = r"""
const probe = arguments[0];
const el = [...document.querySelectorAll("div[aria-label='Message Body'], div[g_editable='true']")]
  .find(e => e.offsetWidth > 0 && e.offsetHeight > 0);
if (!el) return "no-body";
const squash = t => (t || "").toLowerCase().replace(/\s+/g, "");
const all = squash(el.textContent);
const sig = el.querySelector("div.gmail_signature, [data-smartmail='gmail_signature']");
if (!sig) return all.includes(probe) ? "ok-no-signature" : "missing";
const r = document.createRange();
r.setStart(el, 0);
r.setEndBefore(sig);
if (squash(r.toString()).includes(probe)) return "ok";
return all.includes(probe) ? "below-signature" : "missing";
"""


def squash(s: str) -> str:
    """Lowercase, every whitespace run removed. Gmail stores paragraphs as separate blocks and reads
    them back with no space between, so any comparison must not depend on whitespace."""
    return re.sub(r"\s+", "", (s or "").lower())


def body_ok(driver, content: str) -> tuple[bool, str]:
    """Prove the message is present AND sits above the signature.

    "The body is not empty" proves nothing: the signature alone makes it non-empty. So look for the
    message's own opening words, and compare against the position of Gmail's signature block.
    """
    probe = squash(" ".join(content.split()[:6]))
    try:
        state = driver.execute_script(_BODY_CHECK_JS, probe)
    except WebDriverException as e:
        return False, f"could not read the compose body: {e}"
    return {
        "ok": (True, "ok (message above signature)"),
        "ok-no-signature": (True, "ok (this account has no Gmail signature)"),
        "below-signature": (False, "the message landed BELOW the signature"),
        "missing": (False, "the message text is not in the compose body"),
        "no-body": (False, "no compose body found"),
    }.get(state, (False, f"unexpected body state: {state}"))


def recipient_present(driver, addr: str) -> bool:
    """Is `addr` committed as a recipient chip (`span[email=...]`)?

    Page-wide, which is right only for the full-screen compose, where the compose is the whole page.
    """
    a = addr.strip().lower()
    for css in ("span[email]", "div[email]", "[data-hovercard-id]"):
        for e in driver.find_elements(By.CSS_SELECTOR, css):
            try:
                v = (e.get_attribute("email") or e.get_attribute("data-hovercard-id") or "").strip().lower()
                if v == a:
                    return True
            except WebDriverException:
                continue
    return False


def _page_text(driver) -> str:
    try:
        return " ".join((driver.find_element(By.TAG_NAME, "body").text or "").split())
    except WebDriverException:
        return ""


def send_button(driver):
    for sel in ("div[role='button'][aria-label^='Send']", "div[role='button'][data-tooltip^='Send']"):
        for el in driver.find_elements(By.CSS_SELECTOR, sel):
            try:
                if el.is_displayed():
                    return el
            except WebDriverException:
                continue
    return None


def attach(driver, paths: Sequence[str]) -> None:
    """Attach files through the compose's hidden file input, then WAIT for the uploads to finish."""
    inputs = driver.find_elements(By.CSS_SELECTOR, "input[type='file']")
    if not inputs:
        raise RuntimeError("no file input in the compose window")
    inputs[-1].send_keys("\n".join(os.path.abspath(p) for p in paths))
    names = [os.path.basename(p) for p in paths]
    end = time.time() + UPLOAD_TIMEOUT
    while time.time() < end:
        # Done when every filename chip shows AND Send is no longer greyed out.
        txt = _page_text(driver)
        btn = send_button(driver)
        if all(n in txt for n in names) and btn is not None \
                and btn.get_attribute("aria-disabled") != "true":
            return
        time.sleep(1.5)
    raise RuntimeError(f"attachment upload did not finish: {names}")


def confirm_sent(driver, timeout: int = 30) -> bool:
    """Sent == Gmail's 'Message sent' notice appears, or our compose window closes.

    NEVER "no dialog is open": the full-screen compose is not a dialog, so that is true from the first
    millisecond and would report every message as sent whether it went or not.
    """
    end = time.time() + timeout
    while time.time() < end:
        try:
            if driver.find_elements(By.XPATH, "//span[contains(text(),'Message sent')]"):
                return True
            if not open_composes(driver):
                return True
        except WebDriverException:
            pass
        time.sleep(1)
    return False


def discard(driver) -> None:
    """Throw the draft away so practice runs leave the mailbox clean."""
    for el in driver.find_elements(By.CSS_SELECTOR,
                                   "div[role='button'][aria-label*='Discard'], img[aria-label*='Discard']"):
        try:
            if el.is_displayed():
                el.click()
                time.sleep(0.6)
                accept_alert(driver)
                return
        except WebDriverException:
            continue
    try:
        driver.switch_to.active_element.send_keys(Keys.ESCAPE)
    except Exception:
        pass
    for _ in range(3):
        if not accept_alert(driver):
            break


def screenshot(driver, shot_dir: Optional[str], addr: str, suffix: str = "") -> str:
    if not shot_dir:
        return ""
    os.makedirs(shot_dir, exist_ok=True)
    safe = re.sub(r"[^\w.-]", "_", addr.replace("@", "_at_"))
    path = os.path.join(shot_dir, f"{safe}{suffix}.png")
    try:
        driver.save_screenshot(path)
        return path
    except Exception:
        return ""


def prepare(driver, to: str, subject: str, body: str, attachments: Sequence[str] = ()) -> None:
    """Get a fully filled, verified compose window on screen. Raises RuntimeError on any doubt,
    after discarding the draft. Shared by send_one and schedule."""
    close_stale_composes(driver)
    open_compose_url(driver, to, subject)
    if not recipient_present(driver, to):
        discard(driver)
        raise RuntimeError("the recipient did not land in the compose window")
    ours = [b for b in open_composes(driver)
            if (b.get_attribute("value") or "").strip() == subject.strip()]
    if not ours:
        discard(driver)
        raise RuntimeError("the subject did not land in the compose window")
    try:
        insert_body(driver, body)
    except RuntimeError:
        discard(driver)
        raise
    ok, why = body_ok(driver, body)
    if not ok:
        discard(driver)
        raise RuntimeError(why)
    if attachments:
        try:
            attach(driver, attachments)
        except RuntimeError:
            discard(driver)
            raise


def send_one(driver, to: str, subject: str, body: str, attachments: Sequence[str] = (), *,
             send: bool, shot_dir: Optional[str] = None) -> dict:
    """Compose one email and either send it (send=True) or discard it (send=False, a practice run).

    Returns {"to", "status", "note", "screenshot"}. status is one of:
      "sent"      Gmail confirmed it
      "preview"   composed, verified, screenshotted and discarded; nothing sent
      "failed"    something went wrong before sending; nothing sent
      "unconfirmed"  Send was clicked but Gmail never confirmed. Check the Sent folder.
    """
    res = {"to": to, "status": "failed", "note": "", "screenshot": ""}
    for p in attachments:
        if not os.path.isfile(p):
            res["note"] = f"attachment not found: {p}"
            return res
    try:
        prepare(driver, to, subject, body, attachments)
    except Exception as e:  # noqa: BLE001
        res["note"] = str(e)
        return res
    res["screenshot"] = screenshot(driver, shot_dir, to)
    if not send:
        discard(driver)
        res.update(status="preview", note="composed and checked, then discarded (nothing sent)")
        return res
    btn = send_button(driver)
    if btn is not None:
        btn.click()
    else:
        driver.switch_to.active_element.send_keys(MOD, Keys.RETURN)
    if confirm_sent(driver):
        res.update(status="sent", note="sent")
    else:
        res.update(status="unconfirmed",
                   note="clicked Send but Gmail never confirmed; check the Sent folder")
    return res
