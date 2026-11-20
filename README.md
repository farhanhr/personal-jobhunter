# Personal Job Finder

A personal job discovery app for Indonesia that runs entirely on your own computer.
It collects relevant job openings from company career pages you approve, keeps them in
one place, and lets you review them in a simple local dashboard. You stay in control:
it never applies to a job, never messages anyone, and never touches your LinkedIn
account.

## Who this is for

Someone job-hunting in Indonesia who is tired of checking many company career pages by
hand, but who does **not** want another account, cloud service, or automated bot acting
on their behalf.

This is a single-person tool. It is not a multi-user product, a hosted service, or a
recruitment platform.

## What it does

- **Searches only what you approve.** You register the company career sources you want
  to follow and enable them yourself. Sources you have not reviewed are never used.
- **Stays within a strict daily limit.** At most two searches per operational day, with
  a 12-hour gap between them. The day resets at 06:00 Jakarta time. Only one search can
  run at a time.
- **Starts only when you click.** Every search is a deliberate action in your local
  dashboard. Nothing runs on a schedule, in the background, or while you are away.
- **Focuses on your target roles.** You choose one to three primary roles and, if you
  want, explicit aliases for them. The app does not silently widen your search.
- **Keeps results in a Google Sheet you own.** New findings are written into your own
  spreadsheet, where you can sort, annotate, and track applications. The app also keeps
  a local copy so the dashboard stays fast and readable offline.
- **Shows provenance.** Every opening keeps the source it came from, so you can always
  check the original posting before deciding anything.

## What it never does

- It does not apply to jobs, fill in forms, or submit anything.
- It does not contact recruiters or send messages of any kind.
- It does not log in to LinkedIn, scrape LinkedIn, or use browser automation against
  LinkedIn. If you want to keep a hiring post you found yourself, you paste its details
  in by hand.
- It does not bypass access controls, rotate identities, or retry aggressively when a
  site asks it to slow down.
- It does not upload your data anywhere except the Google Sheet you connect yourself.
- It does not send analytics, telemetry, or usage reports.

## How it works

Everything runs locally. A small backend does the searching and bookkeeping, and a local
web interface is where you configure and review. Both listen only on your own machine
(`127.0.0.1`), so nothing is reachable from the internet or your local network.

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
- A Google account, if you want results written to a spreadsheet. This part is optional
  until you decide to connect it.

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

### 2. Describe what you are looking for

In the dashboard, add one to three target roles and register the company career sources
you want to follow. A source must be explicitly reviewed and enabled before it can be
used. You can disable a source at any time.

### 3. Connect your Google Sheet (optional but recommended)

The app never asks for your Google password. It uses Google's own sign-in page in your
browser, and the resulting token is kept in your operating system's credential store.

1. In Google Cloud Console, enable the **Google Sheets API**.
2. Configure an OAuth consent screen. For personal use, **External** with publishing
   status **Testing** is enough while you are the only test user.
3. Create an OAuth client of type **Desktop app** and download its JSON file.
4. Keep that file outside any Git repository, for example at
   `~/.local/share/personal-job-finder/credentials/google-oauth-client.json`.
5. Create a spreadsheet, then point the app at it:

   ```bash
   export GOOGLE_SHEETS_SPREADSHEET_ID='<your-spreadsheet-id>'
   ```

6. Authorize the account from `backend/`:

   ```bash
   uv run python -m personal_job_finder_api.integrations.google_sheets.cli auth
   ```

7. Check that the spreadsheet matches the expected layout. This command only reads;
   it never writes:

   ```bash
   uv run python -m personal_job_finder_api.integrations.google_sheets.cli check
   ```

   If a canonical tab or header row is missing, create the missing tabs and empty
   header rows:

   ```bash
   uv run python -m personal_job_finder_api.integrations.google_sheets.cli init
   ```

   `init` refuses to overwrite a non-empty header row that does not match the expected
   schema. Fix that case by hand; nothing is rewritten for you.

8. Optionally build the workspace: summary cards plus an editable table.

   ```bash
   uv run python -m personal_job_finder_api.integrations.google_sheets.cli dashboard-init
   uv run python -m personal_job_finder_api.integrations.google_sheets.cli sync
   ```

   `dashboard-init` creates the `Dashboard` tab: six read-only cards (need to apply,
   applied in the last 7 days, interviews in the next 7 days, offers, active, archived).
   `sync` then runs the repair pass, which turns `Opportunities` into a native Sheets
   table: banded rows, a frozen header, and a dropdown on `user_status` and
   `application_status`. The dropdowns are suggestions, not constraints — `Ghosted` or
   any other text you type stays exactly as typed.

   You work directly in the `Opportunities` table. Add a row by hand with a company and a
   job title and no `opportunity_id`; the next `sync` gives it a stable identity and keeps
   your values. Set `user_status` to `ignored` to archive a row: it leaves the active
   count and the cards stop counting it, but nothing is deleted.

   The older 25-metric board is still available as the `Metrics` tab:

   ```bash
   uv run python -m personal_job_finder_api.integrations.google_sheets.cli metrics-init
   ```

   An existing `Dashboard` board from an earlier version is renamed to `Metrics`, never
   deleted. Every tab here is a companion view, never a source of truth, and none is
   required.

   To check both tabs without changing anything:

   ```bash
   uv run python -m personal_job_finder_api.integrations.google_sheets.cli dashboard-verify
   ```

   This reads both tabs and both canonical tabs, recomputes every number independently, and
   reports any cell that shows an error, a constant instead of a formula, or a number that
   disagrees. It exits non-zero on a mismatch. A tab written by an older version of this
   app is reported as unsupported: delete that tab and re-run its `-init` command.

9. Copy new results into your sheet whenever you want:

   ```bash
   uv run python -m personal_job_finder_api.integrations.google_sheets.cli sync
   ```

   Or pull your latest sheet edits back into the dashboard:

   ```bash
   curl -X POST http://127.0.0.1:8000/api/v1/sheets/refresh \
     -H 'Origin: http://127.0.0.1:5173'
   ```

To disconnect later, run `... google_sheets.cli disconnect`, or revoke the app's access
from your Google account page. Your local copy is not affected.

### 4. Run a search

Press the search button in the dashboard. The app checks the daily limit and cooldown,
then searches every enabled source for your target roles. When it finishes, new findings
are queued for your sheet and the dashboard shows what was found.

## Your data and privacy

- All settings, limits, and pending work live in
  `~/.local/share/personal-job-finder/` on your computer.
- To keep that directory inside the project instead, set `PJF_DATA_DIR` to a path under
  `.local/`, for example `PJF_DATA_DIR=.local/share/personal-job-finder`. `.local/` and
  every `*.sqlite3` file are ignored by Git, so the data stays on your machine and is
  never committed. Deleting that one directory removes all of it.
- The Google token is stored in your OS credential store. If none is available, it is
  written to a private file that only your user account can read.
- Nothing is sent to the project author or any third party. There is no telemetry.
- Your spreadsheet ID, roles, and search history are yours. They are not part of this
  project and are never published here.
- Uninstalling is enough to remove everything, aside from the Google Sheet you created
  and the app authorization you can revoke in your Google account.

## Good to know

- **Google sign-in may need renewing.** Apps in Google's "Testing" status can expire
  refresh tokens after seven days. If that happens, run the `auth` command again. This
  is expected Google behavior, not a bug in this app.
- **A search is not a guarantee.** A job can close, change, or be filled between the
  search and your application. Always check the original posting.
- **Only what you enabled is searched.** If a result seems missing, check that the
  company source is registered, approved, and enabled.
- **One app process at a time.** Running two copies against the same spreadsheet at once
  is not supported.
- **Tracker dates are plain text.** Write `YYYY-MM-DD` or `DD/MM/YYYY` with the day
  first; a trailing note such as `(pagi)` is ignored when dates are counted. The app
  never rewrites what you typed, and those columns are deliberately not formatted as
  dates: a date format would turn `2026-11-18` into a number the tracker cannot read.
- **The spreadsheet time zone must be `Asia/Jakarta`** for the optional spreadsheet
  `Dashboard` date windows. The app never changes it for you.
- **The spreadsheet locale must be `en_US`, `id_ID`, or `in_ID`** for the optional
  spreadsheet `Dashboard`. `in_ID` is the legacy Google identifier for the same
  Indonesian locale as `id_ID`. Formulas use the locale's argument separator (comma or
  semicolon), so other locales are refused rather than producing broken cells.

## Current status

The app is usable today: target roles, company sources, manual searches with the daily
limit and cooldown, an in-app dashboard, an opportunities feed with a detail view, an
application tracker, Google Sheets sign-in and synchronization, and an optional
workspace `Dashboard` cards and an editable `Opportunities` table in your spreadsheet.

Google Sheets support is implemented and covered by mocked tests, plus an opt-in live
suite that runs a real round-trip against a disposable scratch spreadsheet. The default
`uv run pytest` never contacts Google; treat your first sync against your own sheet as a
supervised setup step.

Still in progress: automatic search history in the sheet (there is no `Run_History` tab
yet) and manual LinkedIn post import. The spreadsheet `Dashboard` is a companion view
only; the app dashboard remains the place for operational state such as search limits,
sync status, and target-role names. You can edit application-tracking fields in the app
or in the sheet; the sheet stays canonical.

## Reporting a security issue

Please do not open a public issue that contains personal data or credentials. See
[SECURITY.md](SECURITY.md) for how to report privately.

## License

MIT. See [LICENSE](LICENSE).
