# codex-gmail-kit

Send email from **your own Gmail**: one message, or a personalised campaign from a spreadsheet.
You tell an AI agent (OpenAI Codex) what you want to send, and it does the setup and the sending for
you. It will not send anything until you say so.

It works by driving a real Chrome window that is signed in to your Gmail. There's no Google Cloud
project, no API keys and no app passwords. You sign in once, like normal.

## Setup (about 10 minutes)

**1. Install three things** (skip any you already have):

- **Google Chrome**: https://www.google.com/chrome/
- **Python 3.9 or newer**: https://www.python.org/downloads/ . On Windows, tick **"Add python.exe to
  PATH"** on the first screen of the installer.
- **OpenAI Codex**: the Codex app, or the Codex CLI (`npm install -g @openai/codex`). Sign in with
  your ChatGPT account.

**2. Get this folder.** Click the green **Code** button on GitHub, then **Download ZIP**, and unzip it
somewhere easy like your Documents folder. (Or `git clone` it if you know how.)

**3. Open the folder in Codex and type:**

> set me up

Codex reads the instructions in this repo (`AGENTS.md`) and takes it from there. It installs what's
needed, opens a Chrome window for you to sign in to Gmail, and asks what you want to send.

**When Codex asks permission to run a command, say yes.** The kit needs the internet and needs to open
Chrome, and both of those are outside Codex's default sandbox. If it keeps asking, switch the
permissions control under the message box to "Full access" for this folder, or type `/permissions` in
the Codex CLI.

## What you can ask for

- "Send this email to everyone in this spreadsheet" (attach the CSV or paste the list)
- "Write a short intro email to these 30 bakeries and personalise the first line"
- "Preview what they'll look like"
- "Send today's batch"
- "Schedule them for Tuesday at 8am"
- "Check who replied"
- "Follow up with the people who didn't answer"
- "Will my emails land in spam?"
- "What's been sent so far?"

## What keeps it safe

These are enforced by the code, not just promised by the AI:

- **Practice mode by default.** Nothing sends without an explicit `--for-real`, and the agent is told to
  get your yes first, every batch.
- **A test to yourself comes first.** Real sending stays locked until one test email to your own address
  goes through.
- **Only your account.** It locks to the Gmail address you signed in with and refuses to send from any
  other.
- **Never the same business twice.** It checks the address, the company's domain, the company name and
  where the lead came from, against everything ever sent.
- **A daily limit** (20 to start) and random gaps between emails, so it looks like a person and protects
  your account's reputation.
- **Stops by itself** if too many emails fail in a row.
- **One follow-up at most**, and never to anyone who replied or bounced.
- **Your password never touches the kit.** You type it into Google's own sign-in page.

## Where things live

- `my/`: your campaigns, recipient lists, send history and screenshots. Stays on your computer; never
  uploaded.
- `.chrome-profile/`: your saved Gmail sign-in. Also never uploaded. Delete it to sign out.
- `kit.py`: every command. Run `python kit.py --help` to see them.

## A word on responsible sending

Automating Gmail's web page is a grey area under Google's terms, so keep volume modest and only
email people with a real reason to hear from you. Commercial email in the US (CAN-SPAM) needs your
physical address and a way to opt out. The easy way is to put both in your Gmail signature, which the
kit keeps on every email. Honour every "please remove me".

## Troubleshooting

Run `python kit.py doctor`. It checks everything and tells you the next step. Or just ask Codex:
"something's wrong, run the doctor".
