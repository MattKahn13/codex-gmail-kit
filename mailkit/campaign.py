"""A campaign = a folder with a template and a recipient list.

    my/campaigns/<name>/
        template.txt      the email, with {placeholders} filled from the spreadsheet
        recipients.csv    one row per person; needs an `email` column
        followup.txt      optional: the one follow-up (same format as template.txt)
        (any attachments the template names)

template.txt looks like this. Header lines first, then a blank line, then the body:

    Subject: Quick question about {company}
    Attach: price-sheet.pdf

    Hi {first_name|there},

    ...

`{column}` is replaced by that column from the row. `{column|fallback}` uses the fallback when the
cell is empty. A placeholder with no value and no fallback REFUSES that row: it is never sent with a
blank where a name should be.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import config

PLACEHOLDER = re.compile(r"\{([A-Za-z0-9_ .-]+?)(?:\|([^{}]*))?\}")
HEADER = re.compile(r"^(subject|attach)\s*:\s*(.*)$", re.I)

DEFAULT_FOLLOWUP = """Subject: Following up

Hi {first_name|there},

Just following up on my last note in case it's of interest. Thanks for your time!
"""


def norm_col(name: str) -> str:
    return re.sub(r"\s+", "_", (name or "").strip().lower())


@dataclass
class Template:
    subject: str
    body: str
    attachments: list = field(default_factory=list)

    @classmethod
    def parse(cls, text: str, base: Path | None = None) -> "Template":
        lines = text.replace("\r\n", "\n").split("\n")
        subject, attach, i = "", [], 0
        while i < len(lines) and lines[i].strip():
            m = HEADER.match(lines[i].strip())
            if not m:
                raise ValueError(
                    f"template line {i + 1} should be 'Subject: ...' or 'Attach: ...', then a blank "
                    f"line, then the body. Got: {lines[i]!r}")
            if m.group(1).lower() == "subject":
                subject = m.group(2).strip()
            else:
                p = Path(m.group(2).strip())
                attach.append(str(p if p.is_absolute() or base is None else base / p))
            i += 1
        body = "\n".join(lines[i:]).strip("\n")
        if not subject:
            raise ValueError("the template has no 'Subject:' line at the top")
        if not body.strip():
            raise ValueError("the template has no body after the blank line")
        return cls(subject, body, attach)

    @classmethod
    def load(cls, path: Path) -> "Template":
        return cls.parse(path.read_text(encoding="utf-8"), base=path.parent)

    def placeholders(self) -> set[str]:
        return {norm_col(m.group(1)) for m in PLACEHOLDER.finditer(self.subject + "\n" + self.body)}

    def render(self, row: dict) -> tuple[str, str]:
        """(subject, body) for one row. Raises ValueError naming every empty placeholder."""
        missing = []

        def fill(m):
            key = norm_col(m.group(1))
            val = (row.get(key) or "").strip()
            if val:
                return val
            if m.group(2) is not None:
                return m.group(2)
            missing.append(key)
            return ""

        subject = PLACEHOLDER.sub(fill, self.subject)
        body = PLACEHOLDER.sub(fill, self.body)
        if missing:
            raise ValueError(f"empty value for {', '.join(sorted(set(missing)))}")
        return subject, body


def load_recipients(path: Path) -> list[dict]:
    """Rows with lowercased, underscore_joined column names. Requires an `email` column."""
    if not path.exists():
        raise FileNotFoundError(f"no recipient list at {path}")
    with open(path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        cols = [norm_col(c) for c in (reader.fieldnames or [])]
        if "email" not in cols:
            raise ValueError(f"{path.name} needs a column named 'email'. Columns found: {cols}")
        rows = []
        for raw in reader:
            row = {norm_col(k): (v or "").strip() for k, v in raw.items() if k is not None}
            if any(row.values()):
                rows.append(row)
    return rows


@dataclass
class Campaign:
    name: str
    folder: Path

    @classmethod
    def open(cls, name: str) -> "Campaign":
        folder = config.CAMPAIGNS / name
        if not folder.is_dir():
            have = sorted(p.name for p in config.CAMPAIGNS.glob("*") if p.is_dir()) \
                if config.CAMPAIGNS.exists() else []
            raise SystemExit(f"No campaign called '{name}' in my/campaigns/. "
                             f"Have: {have or 'none yet'}. Make one with:  python kit.py new {name}")
        return cls(name, folder)

    def template(self) -> Template:
        return Template.load(self.folder / "template.txt")

    def followup_template(self) -> Template:
        p = self.folder / "followup.txt"
        return Template.load(p) if p.exists() else Template.parse(DEFAULT_FOLLOWUP)

    def recipients(self) -> list[dict]:
        return load_recipients(self.folder / "recipients.csv")


@dataclass
class Email:
    row: dict
    to: str
    subject: str
    body: str
    attachments: list


def render_all(tpl: Template, rows: list[dict]) -> tuple[list[Email], list[tuple[dict, str]]]:
    """Render every row. Returns (emails, [(row, why_refused)])."""
    out, bad = [], []
    for r in rows:
        try:
            subject, body = tpl.render(r)
        except ValueError as e:
            bad.append((r, str(e)))
            continue
        out.append(Email(r, r.get("email", ""), subject, body, list(tpl.attachments)))
    return out, bad
