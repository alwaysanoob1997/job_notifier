# LinkedIn Jobs automation (web GUI)

This workspace contains the vendored **[linkedin-jobs-scraper](py-linkedin-jobs-scraper-master/)** library and a small **FastAPI** web app that:

- Scrapes **public** LinkedIn job listings (guest mode; no login). See the upstream [documentation](py-linkedin-jobs-scraper-master/Documentation.md).
- Stores deduplicated jobs in **SQLite** under **`~/LinkedInJobs/jobs.db`** (override with **`LINKEDIN_JOBS_DB_URL`**, e.g. for tests).
- Marks **new** jobs per run: a `job_id` is inserted only once; repeats in later runs count as duplicates.
- Runs **1–5 times per day** on a fixed local-time grid while **`uvicorn` is running**, plus a **manual run** button.
- Offers a **CLI** entry point for cron / systemd / launchd: `python -m app`.

## Requirements

- **Python 3.10+** (3.12 used in CI-style local tests).
- **Google Chrome or Chromium** on the machine (Selenium is used when guest HTTP cannot complete the scrape). Match **ChromeDriver** to your browser major version if Selenium does not resolve it automatically.
- **macOS** or **Linux** (e.g. Ubuntu). WSL2 is supported if Chrome/Chromium and a display or headless setup work in your environment.

## Install

From the repository root (`LinkedInAutomation`):

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

`requirements.txt` installs the vendored scraper from `./py-linkedin-jobs-scraper-master` into the same environment as the web app (a normal install, not editable, so it behaves like any other bundled dependency when you build an image or venv).

## Run the web app

```bash
cd /path/to/LinkedInAutomation
source .venv/bin/activate
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open **http://127.0.0.1:8000**. Set **job title**, **location**, and **runs per day (1–5)**, save settings, then use **Run scraper now**.

Defaults applied by the runner:

- **Time window:** last 24 hours (`TimeFilters.DAY`).
- **Sort:** recent (`RelevanceFilters.RECENT`).
- **Max results:** 100 per run (single location).

### Scheduling note

**APScheduler** registers evenly spaced local-time triggers (e.g. 3 runs → 00:00, 08:00, 16:00). They only fire while the **Uvicorn process is running**. For a machine that is not always running the app, use the CLI with the OS scheduler (see below).

## Headless / cron: `python -m app`

Runs **one** scrape using the same database and saved settings as the web app, then exits:

```bash
source .venv/bin/activate
python -m app
```

Example **systemd** timer or **cron** (adjust paths and user):

```cron
0 8,14,20 * * * cd /path/to/LinkedInAutomation && /path/to/.venv/bin/python -m app >> ~/LinkedInJobs/cron.log 2>&1
```

On **macOS**, use **launchd** with a similar command and `StartCalendarInterval`.

## Tests

```bash
pip install pytest
pytest tests/
```

## Legal

Use public data responsibly. Comply with [LinkedIn’s User Agreement](https://www.linkedin.com/legal/user-agreement) and applicable law. This tool is for personal or research use; the upstream library maintainers disclaim responsibility for misuse.
