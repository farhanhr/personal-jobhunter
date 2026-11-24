# Personal Job Finder

A personal job discovery app for Indonesia that runs entirely on your own computer. It
collects job openings from company career pages you approve, keeps them in one place, and
lets you review them in a local dashboard. You stay in control: it never applies to a job,
never messages anyone, and never touches your LinkedIn account.

## Who this is for

Someone job-hunting in Indonesia who is tired of checking many company career pages by
hand, but who does **not** want another account, cloud service, or automated bot acting on
their behalf.

This is a single-person tool. It is not a multi-user product, a hosted service, or a
recruitment platform.

## What it does

- **Searches only what you approve.** You register the company career sources you want to
  follow and enable them yourself.
- **Stays within a strict daily limit.** At most two searches per operational day, with a
  12-hour gap between them. The day resets at 06:00 Jakarta time, and only one search can
  run at a time.
- **Starts only when you click.** Every search is a deliberate action. Nothing runs on a
  schedule, in the background, or while you are away.
- **Focuses on your target roles.** You choose one to three primary roles and, if you want,
  explicit aliases for them. The app does not silently widen your search.
- **Keeps results in a Google Sheet you own.** New findings are written into your own
  spreadsheet, where you can sort, annotate, and track applications. A local copy keeps the
  dashboard fast and readable offline.
- **Shows provenance.** Every opening keeps the source it came from, so you can check the
  original posting before deciding anything.

## What it never does

- It does not apply to jobs, fill in forms, or submit anything.
- It does not contact recruiters or send messages of any kind.
- It does not log in to LinkedIn, scrape LinkedIn, or use browser automation against
  LinkedIn. If you want to keep a hiring post you found yourself, you paste its details in
  by hand.
- It does not bypass access controls, rotate identities, or retry aggressively when a site
  asks it to slow down.
- It does not upload your data anywhere except the Google Sheet you connect yourself, and it
  sends no analytics or telemetry.

## How it works

Everything runs locally. A small backend does the searching and the bookkeeping, and a
local web interface is where you configure and review. Both listen only on your own
machine (`127.0.0.1`), so nothing is reachable from the internet or your local network.

Two kinds of storage are used, with different jobs:

| Storage | What it holds |
|---|---|
| Your Google Sheet | The record you actually own and read: opportunities, their sources, and search history |
| A local database on your computer | Private settings, search limits, pending synchronization work, and a fast local copy of your sheet |

Your spreadsheet is the source of truth. The local copy can always be rebuilt from it.

## What you need

- A computer running Linux, macOS, or Windows with Python 3.12 and
  [uv](https://docs.astral.sh/uv/) installed.
- Node.js 20.19+ (or 22.12+) and npm, to run the local interface.
- A Google Cloud project with the Sheets API enabled, one Service Account with a JSON key,
  and one spreadsheet shared with that Service Account as **Editor**. Google Sheets is
  required: it is where your opportunities are stored.

No Docker, database server, cloud account, or paid service is required.

## Getting started

### 1. Start the app

```bash
cd backend
uv sync
uv run uvicorn personal_job_finder_api.main:app --host 127.0.0.1 --port 8000
```

Then, in a second terminal:

```bash
cd frontend
npm install
npm run dev
```

Open <http://127.0.0.1:5173>. Your data stays on this machine.

### 2. Connect your Google Sheet (required)

The app stores your opportunities in Google Sheets and keeps them in sync. Google Sheets is
**required** for the first run: until a workspace is connected, the dashboard redirects to
a short onboarding checklist. Settings and onboarding stay reachable.

The app uses a **Google Service Account** key. It does not ask you to sign in to Google,
and it never needs your Google password. You do this setup once.

**What you need**

1. A Google Cloud project (any existing project works).
2. The **Google Sheets API** enabled for that project.
3. One **Service Account** in that project, with one **JSON key** downloaded.
4. Your spreadsheet shared with the Service Account email as **Editor**.

No OAuth client, consent screen, redirect URI, or project IAM role is required.

**Step 1: create the key**

1. In Google Cloud Console, open **IAM & Admin → Service Accounts** and create a service
   account. Any name is fine.
2. Open the service account, go to **Keys → Add key → Create new key**, choose **JSON**, and
   download the file.

Be sure you download a **Service Account** key. A file with an `installed` or `web` section
is an OAuth client file and is rejected.

**Step 2: share the spreadsheet**

1. Copy the `client_email` shown in the key or in Settings. It ends in
   `.iam.gserviceaccount.com`.
2. In Google Sheets, click **Share**, paste that email, set the role to **Editor**, and
   send.

**Step 3: connect the spreadsheet**

Use either path. Both call the same setup service and store the same local state.

- **Settings UI (primary):** open Settings → Google Sheets, enter the local setup token
  (see below), upload the key, paste the spreadsheet URL, and click **Save & Setup**. The
  page shows **Ready** with the refreshed row counts, or tells you which step to fix.
- **Command line:**

  ```bash
  cd backend
  uv run job-hunter setup \
    --key-file ~/Downloads/service-account.json \
    --spreadsheet-url "https://docs.google.com/spreadsheets/d/…/edit"
  ```

  `--key-file` copies the key to the canonical path below (mode `600`).
  `--spreadsheet-url` validates and adopts that spreadsheet. Without flags, the command uses
  the saved or `.env` configuration. For a CLI-only setup, you can keep the link in `.env`
  (`GOOGLE_SHEETS_SPREADSHEET_URL`) and omit `--spreadsheet-url`; the app reads it and
  never rewrites the file. If you later change that value, run `job-hunter setup` again:
  the app refuses to use a sheet it has not validated. `uv run job-hunter doctor` prints
  readiness at any time.

**The canonical key path**

```
.local/credentials/google-service-account.json
```

The `.local/` folder is ignored by Git. The application restricts the file to your user
account (mode `600`). Do not place the key anywhere else, and do not commit it.

Two ways to provide the key, both landing on the same path with the same permissions:

- **Settings UI (recommended):** upload the downloaded JSON in Settings → Google Sheets.
- **Manually:** copy the JSON to `.local/credentials/google-service-account.json` and make
  sure only your user can read it (`chmod 600`). The app picks it up on the next start;
  `uv run job-hunter doctor` confirms it was read.

**You never have to rename the file.** Google names the download after your project, for
example `project-test-515302-fd83b35465ce.json`. Whatever the file is called, the upload
stores it at the canonical path above and nowhere else. The original filename is never
kept as a second credential file, and the browser never sends it to the server at all.

Uploading a new key **replaces** the installed one in place: the file is overwritten
atomically, so there is still exactly one credential. Before the replacement is accepted,
the JSON is parsed and checked as a Service Account key; an OAuth client file, malformed
JSON, or a file over 64 KB is rejected and your existing key is left untouched.

After a replacement, the app re-checks that the **new** Service Account can open the
spreadsheet. If it cannot, the page says which email to share as Editor and the workspace
is marked not ready until setup succeeds again — it never reports "ready" on the strength
of the key you just replaced.

`PJF_LOCAL_DIR` relocates the credential directory. It exists so that isolated checks and
the live E2E harness never touch your real key file; leave it unset for normal use.

**Existing spreadsheets are adopted**

- A blank spreadsheet gets the required tabs, headers, KPI strip, and the optional
  `Metrics` board.
- A spreadsheet this app used before is adopted: its rows are refreshed into the app and
  new rows append below them.
- A spreadsheet with data that is not a Personal JobHunter workspace is refused with
  `workspace_incompatible`. Nothing is written and the previous workspace stays active.

**The local setup token**

The Settings section is protected so that only you, on this computer, can change Google
settings. When the server starts, it writes a one-time token to a private file under your
data directory (`~/.local/share/personal-job-finder/setup/bootstrap-token`, or the path set
by `PJF_DATA_DIR`). Paste that value into Settings. The token works once, expires after 30
minutes, and the file is removed after you use it. Restarting the server issues a new token.

**Setup stages**

If setup stops, the page or CLI says which stage failed:

| Stage | Meaning | What to do |
|---|---|---|
| credentials | No key, or the file is not a Service Account key | Install the correct JSON key |
| spreadsheet | The URL is not a Google Sheets link | Paste the full `https://docs.google.com/spreadsheets/d/...` address |
| access | Google denied access | Share the spreadsheet with the `client_email` as Editor, then retry |
| workspace | The target is not a Personal JobHunter workspace | Use a blank spreadsheet or one this app created |
| schema | A tab exists with headers the app does not recognize | Fix that header row by hand; nothing was changed |

Every stage is safe to retry, and a failed setup never replaces the previously active
spreadsheet.

**If access is revoked later**

Deleting the key or removing the Service Account from the spreadsheet share does not lock
the app. Data already synchronized stays readable; only refresh/sync fails, with a stable
error (`credentials_missing`, `permission_denied`). Restore the key or the share and run
**Save & Setup** again.

**Advanced commands**

The retained developer commands still run from `backend/` with `uv run job-hunter ...`
(for example `job-hunter doctor`). The Google Sheets maintenance commands below use the
same configuration:

```bash
uv run python -m personal_job_finder_api.integrations.google_sheets.cli sync
uv run python -m personal_job_finder_api.integrations.google_sheets.cli workspace-repair
uv run python -m personal_job_finder_api.integrations.google_sheets.cli metrics-init
uv run python -m personal_job_finder_api.integrations.google_sheets.cli dashboard-verify
```

`sync` copies new results into your sheet, and `workspace-repair` restores the workspace
layout without changing any data. `metrics-init` creates the optional `Metrics` board.
`dashboard-verify` checks the KPI strip and the board without writing anything.

To remove the key, use Settings → Disconnect. Your spreadsheet is not changed.

### 3. Describe what you are looking for

Now that the workspace is ready, add one to three target roles and register the company
career sources you want to follow. A source must be explicitly reviewed and enabled before
it can be used. You can disable a source at any time.

### 4. Run a search

Press the search button in the dashboard. The app checks the daily limit and cooldown,
then searches every enabled source for your target roles. When it finishes, new findings
are queued for your sheet and the dashboard shows what was found.

## Your data and privacy

- All settings, limits, and pending work live in `~/.local/share/personal-job-finder/` on
  your computer. To keep that directory inside the project instead, set `PJF_DATA_DIR` to a
  path under `.local/`, for example `PJF_DATA_DIR=.local/share/personal-job-finder`.
  `.local/` and every `*.sqlite3` file are ignored by Git, so the data stays on your
  machine and is never committed. Deleting that one directory removes all of it.
- The Google Service Account key is stored only at
  `.local/credentials/google-service-account.json` (ignored by Git, mode `600`). The app
  never shows its contents and never sends it anywhere other than Google's Sheets API.
- Nothing is sent to the project author or any third party. There is no telemetry.
- Your spreadsheet ID, roles, and search history are yours. They are not part of this
  project and are never published here.
- Uninstalling is enough to remove everything, aside from the Google Sheet you created. To
  revoke the key, delete it in Google Cloud Console and remove the local file.

## Good to know

- **A search is not a guarantee.** A job can close, change, or be filled between the search
  and your application. Always check the original posting.
- **Only what you enabled is searched.** If a result seems missing, check that the company
  source is registered, approved, and enabled.
- **One app process at a time.** Running two copies against the same spreadsheet at once
  is not supported.
- **Tracker dates are plain text.** Write `YYYY-MM-DD` or `DD/MM/YYYY` with the day first; a
  trailing note such as `(pagi)` is ignored when dates are counted. The app never rewrites
  what you typed, and those columns are deliberately not formatted as dates.
- **The spreadsheet time zone must be `Asia/Jakarta`** for the KPI strip and the optional
  `Metrics` date windows. The app never changes it for you.
- **The spreadsheet locale must be `en_US`, `id_ID`, or `in_ID`** for the KPI strip and the
  optional `Metrics` board. `in_ID` is the legacy Google identifier for the same Indonesian
  locale as `id_ID`. Other locales are refused rather than producing broken cells.

## Current status

The app is usable today: mandatory Google Sheets onboarding, target roles, company
sources, manual searches with the daily limit and cooldown, an in-app dashboard, an
opportunities feed with a detail view, an application tracker, Google Sheets
synchronization with a Service Account key, and an optional KPI strip and an editable
`Opportunities` table in your spreadsheet.

The first run is gated: until a spreadsheet workspace is validated and prepared, the main
pages redirect to the onboarding checklist. An existing spreadsheet this app used before is
adopted and continued; an incompatible spreadsheet is refused without any write. If the key
or the share is revoked later, already-synchronized data stays readable and only
refresh/sync fails.

Google Sheets support is implemented and covered by mocked tests, plus an opt-in live suite
that runs a real round-trip against a disposable scratch spreadsheet. The default
`uv run pytest` never contacts Google; treat your first sync against your own sheet as a
supervised setup step.

## Reporting a security issue

Please do not open a public issue that contains personal data or credentials. See
[SECURITY.md](SECURITY.md) for how to report privately.

## License

MIT. See [LICENSE](LICENSE).
