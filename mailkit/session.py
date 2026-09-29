"""Signing in, and proving which account is signed in before anything is sent.

The golden rule: VERIFY the active account, never assume it. Gmail's `u/0` is whichever account
signed in first in that browser, and the page happily loads the wrong one. The page title carries the
address ("Inbox (3) - you@example.com - Gmail"), so that is what gets read.
"""
from __future__ import annotations

import re
import time

from selenium.webdriver.common.by import By
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.support.ui import WebDriverWait

from . import config
from .browser import PAGE_TIMEOUT, build_driver, quit_driver

EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.\w{2,}")

# Gmail's Compose button. The class soup drifts; the compound `T-I-KE` is the discriminator (bare
# `.L3` or `.T-I` match a dozen toolbar buttons). `gh='cm'` was the classic handle and is dead.
COMPOSE_SELECTORS = ("div.T-I.T-I-KE.L3", "div[gh='cm']", "[aria-label='Compose']",
                     "[aria-label^='Compose']")


def active_account(driver) -> str:
    """The signed-in address, read off the page. '' if it cannot be read."""
    m = EMAIL_RE.search(driver.title or "")
    if m:
        return m.group(0).lower()
    try:
        for el in driver.find_elements(By.CSS_SELECTOR,
                                       "a[aria-label*='Account'], [aria-label*='Google Account']"):
            m = EMAIL_RE.search(el.get_attribute("aria-label") or "")
            if m:
                return m.group(0).lower()
    except WebDriverException:
        pass
    return ""


def compose_button(driver):
    for sel in COMPOSE_SELECTORS:
        try:
            for el in driver.find_elements(By.CSS_SELECTOR, sel):
                if el.is_displayed():
                    return el
        except WebDriverException:
            continue
    try:
        for el in driver.find_elements(By.CSS_SELECTOR, "div[role='button'], div.T-I"):
            try:
                if el.is_displayed() and (el.text or "").strip().lower() == "compose":
                    return el
            except WebDriverException:
                continue
    except WebDriverException:
        pass
    return None


def is_logged_in(driver) -> bool:
    """True once a signed-in inbox is open. Keyed off the URL and the address in the title, which
    are durable; any single button is not. (The page source contains "sign in" even when signed in,
    so never search the HTML for it.)"""
    url = (driver.current_url or "").lower()
    if "accounts.google.com" in url or "signin" in url or "servicelogin" in url:
        return False
    if "mail.google.com/mail" not in url:
        return False
    if EMAIL_RE.search(driver.title or ""):
        return True
    return compose_button(driver) is not None


def wait_ui_ready(driver, timeout: int = 40) -> None:
    """Wait until Gmail is INTERACTIVE, not merely loaded.

    The title and URL are set long before Gmail attaches its click handlers. Composing the instant
    the page is identified races the UI, and the failure then shows up one step later under a
    misleading name. Gate on readyState + a real Compose button + a short settle.
    """
    end = time.time() + timeout
    while time.time() < end:
        try:
            if (driver.execute_script("return document.readyState") == "complete"
                    and compose_button(driver) is not None):
                time.sleep(1.5)
                return
        except WebDriverException:
            pass
        time.sleep(0.5)


def open_gmail(driver, sender: str) -> str:
    """Load Gmail and refuse to continue unless `sender` is the signed-in account.

    Returns the active address. Raises RuntimeError with a plain-English reason otherwise.
    """
    driver.get(config.GMAIL_URL)
    try:
        WebDriverWait(driver, PAGE_TIMEOUT).until(
            lambda d: is_logged_in(d) or "accounts.google.com" in (d.current_url or "").lower())
    except TimeoutException:
        pass
    if not is_logged_in(driver):
        raise RuntimeError("Not signed in to Gmail. Run:  python kit.py login")
    acct = active_account(driver)
    if not sender:
        raise RuntimeError("No sender is set in my/settings.json. Run:  python kit.py login")
    if acct != sender.lower():
        raise RuntimeError(
            f"Gmail is signed in as '{acct or 'unknown'}', but this kit is locked to send from "
            f"'{sender}'. Refusing to send from the wrong address. Run `python kit.py login` to "
            f"sign in as {sender}, or `python kit.py login --switch` to change the sender.")
    wait_ui_ready(driver)
    return acct


def login(switch: bool = False, wait_minutes: int = 10) -> int:
    """Open a normal Chrome window on the kit profile and wait for the user to sign in.

    No Enter key needed: it watches the window and finishes by itself once an inbox is open, so an
    AI agent running this command does not have to type anything. The password is typed by the
    person, in Google's own page. Nothing here ever sees it.
    """
    settings = config.load_settings()
    driver = build_driver()
    try:
        driver.get(config.GMAIL_URL)
        print("\nA Chrome window just opened.")
        print("  Sign in to the Gmail account you want to send from, in that window.")
        print("  Finish any 2-step verification Google asks for.")
        print(f"  This will wait up to {wait_minutes} minutes and finish by itself.\n", flush=True)
        end = time.time() + wait_minutes * 60
        last_note = 0.0
        while time.time() < end:
            try:
                if is_logged_in(driver) and active_account(driver):
                    break
            except WebDriverException:
                pass
            if time.time() - last_note > 30:
                print("  ...still waiting for the inbox to appear", flush=True)
                last_note = time.time()
            time.sleep(3)
        else:
            print("\nTimed out before an inbox appeared. Run `python kit.py login` again.")
            return 1

        acct = active_account(driver)
        old = settings.get("sender", "")
        if old and old != acct and not switch:
            print(f"\nSigned in as {acct}, but the kit is locked to {old}.")
            print("Nothing changed. To make the new address the sender, run:")
            print("  python kit.py login --switch")
            return 1
        settings["sender"] = acct
        config.save_settings(settings)
        print(f"\nSigned in as {acct}. The login is saved, and {acct} is now the only address")
        print("this kit will send from. You will not need to sign in again unless Google logs you out.")
        return 0
    finally:
        quit_driver(driver)


def whoami() -> int:
    """Report the saved login without changing anything."""
    settings = config.load_settings()
    driver = build_driver(headless=True)
    try:
        driver.get(config.GMAIL_URL)
        end = time.time() + 25
        while time.time() < end and not is_logged_in(driver):
            time.sleep(1)
        if not is_logged_in(driver):
            print("Not signed in. Run:  python kit.py login")
            return 1
        acct = active_account(driver)
        sender = settings.get("sender", "")
        ok = bool(sender) and acct == sender.lower()
        print(f"Signed in as: {acct or '(could not read)'}")
        print(f"Locked sender: {sender or '(none yet)'}")
        print("READY" if ok else "NOT READY: run `python kit.py login`")
        return 0 if ok else 1
    finally:
        quit_driver(driver)
