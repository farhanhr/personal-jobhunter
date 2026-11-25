# Personal JobHunter

A local-first job discovery app for Indonesia. It collects openings from company career
pages you approve, keeps them in one place, and lets you review them in a local dashboard.

It never applies to a job, messages a recruiter, or touches your LinkedIn account. All
data stays on your computer except the Google Sheet you connect yourself.

## Quick start

### 1. Clone

```bash
git clone https://github.com/farhanhr/personal-jobhunter.git
cd personal-jobhunter
```

### 2. Start

```bash
./run.sh
```

`run.sh` installs the Python and Node.js dependencies on first run, then starts both the
backend and the web interface. Press `Ctrl+C` to stop them.

**What you need:** Linux, macOS, or Windows with WSL/Git Bash, plus:

- [uv](https://docs.astral.sh/uv/) for Python
- Node.js 20.19+ (or 22.12+) and npm
- A Google account and one Google Sheet

No Docker, database server, or paid service is required.

### 3. Configure in the browser

Open <http://localhost:5173>, then:

1. Go to **Settings → Google Sheets**.
2. Upload the Service Account JSON key.
3. Paste your Google Spreadsheet URL.
4. Click **Save & Connect Workspace**.

The app shows whether the key, the spreadsheet, and the workspace are ready. Once the
workspace is ready, add your target roles and company sources, then run your first search.

## Connect Google Sheets (2 minutes)

The app uses a **Service Account** key. It does not ask for your Google password and never
needs an OAuth consent screen.

1. Open the [Google Cloud Console](https://console.cloud.google.com/) and create a project
   (or pick an existing one).
2. [Enable the Google Sheets API](https://console.cloud.google.com/apis/library/sheets.googleapis.com)
   for that project.
3. Go to **IAM & Admin → Service Accounts → Create service account**. Any name works.
4. Open the new service account, then **Keys → Add key → Create new key → JSON**. Download
   the file. Keep it private.
5. Create a blank Google Spreadsheet, click **Share**, and share it with the Service
   Account email (it ends in `.iam.gserviceaccount.com`) as **Editor**.
6. In the app, open **Settings → Google Sheets**, upload the downloaded JSON, paste the
   spreadsheet link, and click **Save & Connect Workspace**.

The key is stored only at `.local/credentials/google-service-account.json` with mode `600`
and is never sent anywhere except Google's Sheets API. Uploading a new key replaces the old
one atomically.

If setup stops, the app says which step failed:

| Stage | What it means | What to do |
|---|---|---|
| credentials | No key, or the file is not a Service Account key | Upload the correct JSON key |
| spreadsheet | The link is not a Google Sheets URL | Paste the full `https://docs.google.com/spreadsheets/d/...` address |
| access | Google denied access | Share the sheet with the Service Account email as Editor |
| workspace | The sheet is not a Personal JobHunter workspace | Use a blank sheet or one this app created |
| schema | A tab has headers the app does not recognize | Fix the header row; nothing was changed |

## Optional AI features

Everything above works without AI. If you want candidate-fit scoring and tailored ATS
resumes, configure them in **Settings**:

1. **AI Provider:** choose Groq, OpenRouter, Gemini, Ollama, or any OpenAI-compatible
   endpoint, then enter the model and API key (Ollama needs no key).
2. **Master CV:** upload a PDF or paste your CV text once.

Then, on any job detail page, use **Analyze Fit** or **Tailor CV**. A score of 90+ appears
as **Best Fit** and can be used as a feed sort order. Without an AI provider or Master CV,
those buttons show an **Unlock AI Features** guide with direct links to Settings; search,
filtering, tracking, and Google Sheets sync keep working normally.

AI requests go only to the provider you configure. Your CV and API key stay in the local
database.

## What it does

- Searches only the company career sources you explicitly register and enable.
- Accepts at most **two searches per operational day**, with a **12-hour cooldown**.
  The day resets at **06:00 Asia/Jakarta**.
- Starts only when you click, and runs one search at a time.
- Focuses on one to three primary roles and their explicit aliases.
- Writes results into your own Google Sheet and keeps a fast local read copy.
- Keeps source provenance so you can check the original posting.

## What it never does

- Does not apply, submit forms, or contact anyone.
- Does not log in to LinkedIn, scrape LinkedIn, or use browser automation against it.
- Does not bypass access controls, rotate identities, or retry aggressively on `429`.
- Does not send analytics or telemetry.

## Your data and privacy

- Settings, search limits, and pending sync work live in
  `~/.local/share/personal-job-finder/` by default. Set `PJF_DATA_DIR` to keep them inside
  the project (for example `.local/share/personal-job-finder`).
- The Service Account key lives only under `.local/credentials/` (ignored by Git).
- Your spreadsheet, roles, and search history are yours and are never published here.
- Deleting the local data directory and revoking the Google key removes everything the app
  stored.

## Everyday use

1. **Target roles:** choose one to three roles and optional aliases.
2. **Company sources:** register and enable the career pages you trust.
3. **Search:** click the search button on the dashboard. The app checks the daily limit and
   cooldown, searches every enabled source, and queues new findings for your sheet.
4. **Review:** use the Main feed and Review queue, then track applications in the detail
   view. Tracker dates are plain text; write `YYYY-MM-DD` or `DD/MM/YYYY`.

## Troubleshooting

- **`uv: command not found`** — install uv with
  `curl -LsSf https://astral.sh/uv/install.sh | sh`, then rerun `./run.sh`.
- **`node`/`npm` missing** — install Node.js 20.19+ from <https://nodejs.org/en/download>.
- **Port already in use** — stop the other process on port `5173` or `8000`.
- **`permission_denied` during setup** — share the spreadsheet with the Service Account
  email as Editor, then click **Save & Connect Workspace** again.
- **`workspace_incompatible`** — the sheet has data this app does not recognize. Nothing was
  changed; use a blank sheet or one the app created.
- **AI buttons show the unlock guide** — add an AI provider and upload your Master CV in
  Settings. Core job discovery does not need them.

## Development

```bash
# Backend
cd backend
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .

# Frontend
cd frontend
npm install
npm test
npm run lint && npm run typecheck
```

Useful backend commands:

```bash
cd backend
uv run job-hunter doctor   # read-only readiness check
uv run job-hunter setup --key-file PATH --spreadsheet-url URL
```

Tests never call live job boards, Google, or an AI provider; they use sanitized fixtures and
mocks.

## Security

Please do not open a public issue containing personal data or credentials. See
[SECURITY.md](SECURITY.md) for private reporting.

## License

MIT. See [LICENSE](LICENSE).
