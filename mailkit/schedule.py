"""Gmail's own "Schedule send": the email waits on Google's servers and goes out at the chosen time,
even if this computer is off.

The page elements (measured on a live Gmail):
  arrow      div[role=button][aria-label='More send options']   (needs a NATIVE click)
  menu item  div[role=menuitem] 'Schedule send'
  dialog     div[role=dialog][aria-label='Schedule send'], then 'Pick date & time'
  picker     td[role=gridcell][aria-label='3 Aug']  +  input[aria-label='Time']
  confirm    button[data-mdc-dialog-action='ok']
  rule       the time must be at least 2 minutes in the future

Gmail's menus swallow the first click often enough that every stage is click, verify, retry.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import WebDriverException

from .compose import (MOD, accept_alert, close_stale_composes, discard, open_composes, prepare,
                      screenshot)


def _click_native(driver, el):
    try:
        el.click()
    except WebDriverException:
        driver.execute_script("arguments[0].click();", el)


def _visible_by_text(driver, css, needle):
    needle = needle.lower()
    for e in driver.find_elements(By.CSS_SELECTOR, css):
        try:
            if e.is_displayed() and needle in (e.text or "").strip().lower():
                return e
        except WebDriverException:
            continue
    return None


def _exact_text(driver, text, tags=("div[role='menuitem']", "span", "div", "li")):
    want = text.strip().lower()
    for css in tags:
        for e in driver.find_elements(By.CSS_SELECTOR, css):
            try:
                if e.is_displayed() and (e.text or "").strip().lower() == want:
                    return e
            except WebDriverException:
                continue
    return None


def _poll(fn, timeout=6.0, step=0.3):
    end = time.time() + timeout
    val = None
    while time.time() < end:
        val = fn()
        if val:
            return val
        time.sleep(step)
    return val


def _click_and_wait(click_fn, verify_fn, tries=4, settle=3.0) -> bool:
    for _ in range(tries):
        try:
            click_fn()
        except Exception:
            pass
        if _poll(verify_fn, timeout=settle):
            return True
        time.sleep(0.5)
    return False


def _open_more_send_menu(driver, tries=4) -> bool:
    for _ in range(tries):
        arrows = [a for a in driver.find_elements(
            By.CSS_SELECTOR, "div[role='button'][aria-label='More send options']") if a.is_displayed()]
        if not arrows:
            time.sleep(0.5)
            continue
        _click_native(driver, arrows[0])
        time.sleep(2.0)
        if _visible_by_text(driver, "div[role='menuitem']", "schedule send"):
            return True
        try:
            driver.switch_to.active_element.send_keys(Keys.ESCAPE)
        except Exception:
            pass
        time.sleep(0.6)
    return False


def _click_schedule_item(driver):
    el = _visible_by_text(driver, "div[role='menuitem']", "schedule send")
    if el is None:
        _open_more_send_menu(driver)
        el = _visible_by_text(driver, "div[role='menuitem']", "schedule send")
    if el is not None:
        _click_native(driver, el)


def _schedule_dialog_open(driver) -> bool:
    for d in driver.find_elements(By.CSS_SELECTOR, "div[role='dialog']"):
        try:
            if d.is_displayed() and (d.get_attribute("aria-label") or "").strip().lower() == "schedule send":
                return True
        except WebDriverException:
            continue
    return False


def _click_pick_date(driver):
    el = (_visible_by_text(driver, "div[role='menuitem']", "pick date")
          or _exact_text(driver, "Pick date & time"))
    if el is not None:
        _click_native(driver, el)


def _time_input(driver):
    for e in driver.find_elements(By.CSS_SELECTOR, "input[aria-label='Time']"):
        try:
            if e.is_displayed():
                return e
        except WebDriverException:
            continue
    return None


def _cleanup(driver):
    accept_alert(driver)
    for _ in range(3):
        cancelled = False
        for e in driver.find_elements(By.CSS_SELECTOR, "button[data-mdc-dialog-action='cancel']"):
            try:
                if e.is_displayed():
                    e.click()
                    cancelled = True
                    time.sleep(0.3)
                    break
            except WebDriverException:
                continue
        if not cancelled:
            break
    try:
        discard(driver)
    except Exception:
        pass
    try:
        close_stale_composes(driver, timeout=6)
    except Exception:
        accept_alert(driver)


def _time_str(dt: datetime) -> str:
    h = dt.hour % 12 or 12
    return f"{h}:{dt.minute:02d} {'AM' if dt.hour < 12 else 'PM'}"


def _confirm_scheduled(driver, timeout=25) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            for sp in driver.find_elements(By.CSS_SELECTOR, "span"):
                t = (sp.text or "").lower()
                if sp.is_displayed() and ("will be sent" in t or "send scheduled" in t):
                    return True
            if not open_composes(driver):
                return True
        except WebDriverException:
            pass
        time.sleep(1)
    return False


def schedule_one(driver, to: str, subject: str, body: str, when: datetime, attachments=(), *,
                 for_real: bool, shot_dir=None) -> dict:
    """Compose to `to` and schedule it for `when` (this computer's local time).

    for_real=False composes, verifies and discards, like a preview.
    """
    res = {"to": to, "when": when.isoformat(timespec="minutes"), "status": "failed", "note": ""}
    if when < datetime.now() + timedelta(minutes=3):
        res["note"] = "the time must be at least 3 minutes from now"
        return res
    accept_alert(driver)
    try:
        prepare(driver, to, subject, body, attachments)
    except Exception as e:  # noqa: BLE001
        res["note"] = str(e)
        return res
    if not for_real:
        screenshot(driver, shot_dir, to)
        discard(driver)
        res.update(status="preview", note="composed and checked, then discarded (nothing scheduled)")
        return res

    if not _open_more_send_menu(driver):
        res["note"] = "the 'More send options' menu did not open"
        _cleanup(driver)
        return res
    if not _click_and_wait(lambda: _click_schedule_item(driver),
                           lambda: _schedule_dialog_open(driver), tries=6, settle=2.5):
        res["note"] = "the Schedule send dialog did not open"
        _cleanup(driver)
        return res
    if not _click_and_wait(lambda: _click_pick_date(driver),
                           lambda: _time_input(driver), tries=5, settle=2.5):
        res["note"] = "the date picker did not open"
        _cleanup(driver)
        return res

    label = f"{when.day} {when.strftime('%b')}"          # matches the day cell, e.g. "3 Aug"

    def find_cell():
        for e in driver.find_elements(By.CSS_SELECTOR, f"td[role='gridcell'][aria-label='{label}']"):
            try:
                if e.is_displayed():
                    return e
            except WebDriverException:
                continue
        return None

    cell = _poll(find_cell, timeout=5.0)
    if cell is None:
        res["note"] = (f"day '{label}' is not in the visible month. Scheduling only works within the "
                       f"month Gmail's picker shows first (usually the current month).")
        _cleanup(driver)
        return res
    _click_native(driver, cell)
    time.sleep(0.4)

    tfield = _poll(lambda: _time_input(driver), timeout=5.0)
    if tfield is None:
        res["note"] = "no Time field in the picker"
        _cleanup(driver)
        return res
    want = _time_str(when)
    try:
        tfield.click()
        tfield.send_keys(MOD, "a")
        tfield.send_keys(Keys.DELETE)
        tfield.send_keys(want)
        time.sleep(0.9)
        # Commit by clicking the matching suggestion. Never Escape: it closes the whole picker.
        opt = None
        for e in driver.find_elements(By.CSS_SELECTOR, "li[role='option'], div[role='option']"):
            try:
                if e.is_displayed() and (e.text or "").strip().lower() == want.lower():
                    opt = e
                    break
            except WebDriverException:
                continue
        if opt is not None:
            _click_native(driver, opt)
        else:
            tfield.send_keys(Keys.TAB)
        time.sleep(0.4)
    except WebDriverException as e:
        res["note"] = f"could not enter the time: {e}"
        _cleanup(driver)
        return res

    screenshot(driver, shot_dir, to, "_schedule")
    confirm = _poll(lambda: next((e for e in driver.find_elements(
        By.CSS_SELECTOR, "button[data-mdc-dialog-action='ok']") if e.is_displayed()), None), timeout=4.0)
    if confirm is None:
        res["note"] = "no confirm button in the picker"
        _cleanup(driver)
        return res
    _click_native(driver, confirm)
    if _confirm_scheduled(driver):
        res.update(status="scheduled", note=f"scheduled for {want} on {label}")
    else:
        res["note"] = "clicked confirm but Gmail never confirmed; check the Scheduled folder"
        _cleanup(driver)
    return res
