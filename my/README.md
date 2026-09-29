# my/ -- your stuff

Everything in this folder stays on your computer. It is ignored by git, so it can never be pushed to
GitHub by accident. (Only this README is tracked.)

| file | what it is |
|---|---|
| `settings.json` | the address you send from (set by `kit.py login`), your daily limit, the gap between emails, and follow-up timing |
| `campaigns/<name>/` | one folder per campaign: `template.txt`, `recipients.csv`, optional `followup.txt` and attachments |
| `ledger.jsonl` | the permanent record of every email sent and every reply seen. **Never delete or edit it.** It is how the kit knows who has already been contacted. |
| `screenshots/` | pictures of each composed email from previews and sends |

The saved Gmail login is in `.chrome-profile/` at the top of the repo, also ignored by git.
