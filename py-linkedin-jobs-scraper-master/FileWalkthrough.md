# Repository file walkthrough

This document summarizes what each file in the repository is for. Paths are relative to the repository root.

## Root

| File | Purpose |
|------|---------|
| `README.md` | Project documentation: installation, environment variable `LOG_LEVEL`, guest-only scraping, API usage, filters, events, and tests. |
| `LICENSE` | MIT license text for the project. |
| `setup.py` | setuptools packaging: package metadata, listed subpackages, dependency on `selenium>=4.12.0`, Python version classifiers, and README as long description. |
| `requirements.txt` | Runtime and dev dependencies for installs and CI: `selenium`, `wheel`, `pytest`. |
| `pytest.ini` | Registers the `integration` pytest marker; default CI runs `-m "not integration"`. |
| `package.json` | npm scripts used by the maintainer for cleaning build artifacts, building wheels/sdists via conda, TestPyPI upload, and running pytest (conda-specific paths). |
| `.gitignore` | Ignores build outputs, virtualenvs, IDE files, Node artifacts, Python caches, `.env`, `tests/results/`, and similar generated or local files. |

## `.github`

| File | Purpose |
|------|---------|
| `workflows/ci.yml` | GitHub Actions workflow: checkout, Python 3.9, `pip install -r requirements.txt`, optional Docker-based tests via `tests/run_tests.sh` (when `TEST` is not `skip`), build sdist/wheel, and publish to PyPI on `master` when secrets are configured. |
| `FUNDING.yml` | Declares GitHub Sponsors funding link for the maintainer. |

## `examples`

| File | Purpose |
|------|---------|
| `example1.py` | Sample script: wires `LinkedinScraper` event handlers (`DATA`, `ERROR`, `END`), builds `Query` / `QueryOptions` / `QueryFilters` examples, and calls `scraper.run()`. **Note:** it references a `optimize` flag on `QueryOptions` that does not exist in the current `query.py` implementation; the example may need updating to run as-is. |

## `tests`

| File | Purpose |
|------|---------|
| `test_.py` | **Integration** pytest (`pytest.mark.integration`): configures `LinkedinScraper` with real Chrome (headless), registers event listeners, defines `Query` objects with filters, merges global `QueryOptions`, and runs against live LinkedIn. |
| `test_anonymous_guest.py` | Unit tests for guest listing URL helpers, HTML parsing, and guest HTTP utilities (fixtures under `tests/fixtures/`). |
| `test_job_salary_ldjson.py` | Unit tests for JSON-LD salary extraction from job page HTML. |
| `shared.py` | Test helpers: `on_data` validates `EventData` shape and non-empty fields/URLs; `on_error` logs; `on_end` prints completion. |
| `run_tests.sh` | Builds a Docker image from `tests/Dockerfile` and runs the container (unit tests only inside the image). |
| `Dockerfile` | Test image based on `spinlud/python3-selenium-chrome`: copies package and tests, installs `requirements.txt`, runs `pytest -m "not integration"`. |

## `linkedin_jobs_scraper` (main package)

| File | Purpose |
|------|---------|
| `__init__.py` | Public entry point: exports `LinkedinScraper` from `linkedin_scraper`. |
| `linkedin_scraper.py` | Core class `LinkedinScraper`: validates constructor options, uses `GuestStrategy`, builds LinkedIn job search URLs from `Query` + filters, spawns a thread pool, runs the strategy per location (passes `driver=None`; strategy creates Chrome only for Selenium fallback), and exposes an event-emitter API (`on`, `once`, `emit`, `remove_listener`, etc.). Includes stub-style proxy getters/setters (`_proxies` is not initialized in the constructor). |
| `config.py` | Reads `LOG_LEVEL` from the environment; defines logger namespace and resolved logging level for the package. |

### `linkedin_jobs_scraper/query`

| File | Purpose |
|------|---------|
| `query.py` | Defines `QueryFilters` (company URL, relevance, time, job type, experience, remote/hybrid, salary, industry), `QueryOptions` (limit, locations, filters, apply link extraction, skip promoted jobs, page offset), and `Query` (keyword string + options), with validation and merging of global options into per-query options. |
| `__init__.py` | Re-exports `Query`, `QueryOptions`, and `QueryFilters`. |

### `linkedin_jobs_scraper/filters`

| File | Purpose |
|------|---------|
| `filters.py` | Enums whose values are LinkedIn URL/query parameter codes: relevance, posted time, employment type, experience level, on-site/remote, industry, and base salary bands. |
| `__init__.py` | Re-exports all filter enums for convenient imports. |

### `linkedin_jobs_scraper/strategies`

| File | Purpose |
|------|---------|
| `strategy.py` | Abstract `Strategy` base class holding a reference to the scraper and declaring a `run(driver, search_url, query, location, page_offset)` interface. |
| `guest_strategy.py` | Guest/public scraping: guest HTTP listing + job pages first; if needed, Selenium on `jobs/search` with SERP selector variants, cookie consent, pagination, description and apply-link extraction, JSON-LD salary, emits `EventData`. Creates and tears down its own Chrome instance when `driver` is `None` and fallback runs. |
| `__init__.py` | Re-exports `Strategy` and `GuestStrategy`. |

### `linkedin_jobs_scraper/events`

| File | Purpose |
|------|---------|
| `events.py` | `Events` enum (`DATA`, `METRICS`, `END`, `ERROR`); `EventData` named tuple for a single scraped job record; `EventMetrics` for run statistics (processed, failed, missed, skipped). |
| `__init__.py` | Re-exports `Events`, `EventData`, and `EventMetrics`. |

### `linkedin_jobs_scraper/exceptions`

| File | Purpose |
|------|---------|
| `exceptions.py` | `CallbackException` (user callback failed). |
| `__init__.py` | Re-exports `CallbackException`. |

### `linkedin_jobs_scraper/utils`

| File | Purpose |
|------|---------|
| `constants.py` | LinkedIn base URLs, jobs search endpoint, page size, and guest `seeMoreJobPostings` API URL. |
| `url.py` | URL helpers: parse query params, strip query string, merge/override query parameters, extract registrable domain, extract scheme + host for relative URL resolution. |
| `logger.py` | Configures a namespaced logger from `Config`, disables urllib3 insecure warnings, and provides `debug` / `info` / `warn` / `error` helpers with truncated formatting. |
| `text.py` | `normalize_spaces` for cleaning scraped text. |
| `user_agent.py` | Pool of legacy user-agent strings and `get_random_user_agent()` (optional; not used in the main guest flow). |
| `chrome_driver.py` | Default Chrome options (headless, sandbox flags, download blocking, etc.), optional proxy capabilities, `build_driver` factory, and helpers to read Chrome debugger / WebSocket debugger URLs for CDP use. |
| `guest_jobs_http.py` | Public guest listing fetch, HTML card parsing, job page fetch, login-wall heuristics, and query translation for the guest API. |
| `job_salary_ldjson.py` | Parses `JobPosting` JSON-LD from job page HTML for structured salary strings. |

## How the pieces fit together

1. Callers construct `Query` / `QueryOptions` / `QueryFilters` and pass them to `LinkedinScraper.run()`.
2. The scraper builds search URLs and delegates each location to `GuestStrategy` (guest HTTP first; Chrome only if fallback is required).
3. `GuestStrategy` extracts fields and calls `scraper.emit(Events.DATA, …)` (and `Events.ERROR` on failures).
4. `Config` supplies logging level from the environment; `utils` supports URLs, logging, guest HTTP, and browser setup.
