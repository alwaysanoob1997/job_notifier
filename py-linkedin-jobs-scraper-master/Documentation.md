# linkedin-jobs-scraper — module documentation

This document describes how to install, configure, and use the **`linkedin_jobs_scraper`** package (PyPI name: **`linkedin-jobs-scraper`**). It expands on the project README with API-level detail, execution flow, and practical guidance.

---

## 1. Purpose and scope

The library scrapes **public** LinkedIn job listings **without** authentication. It always uses **`GuestStrategy`**: the same information a signed-out visitor can see on `https://www.linkedin.com/jobs/search` and on public job view URLs.

**Intended use:** personal, research, or educational projects. Job listing data is controlled by LinkedIn; comply with their [Terms of Use](https://www.linkedin.com/legal/user-agreement), applicable law, and reasonable rate limits. The maintainers are not responsible for misuse.

**What this module does *not* do:** it does not log in, bypass paywalls, scrape private or member-only fields, or guarantee stable HTML/API contracts—LinkedIn can change responses at any time.

---

## 2. Architecture overview

### 2.1 High-level flow

1. You construct one or more **`Query`** objects and call **`LinkedinScraper.run(queries, options=None)`**.
2. For each query, the scraper iterates **`QueryOptions.locations`** (or a single merged default location list).
3. For each `(query, location)` pair it builds a **jobs search URL** (same query parameters as the website) via **`LinkedinScraper`** internals, then delegates to **`GuestStrategy.run()`** in `linkedin_jobs_scraper/strategies/guest_strategy.py`.
4. **`GuestStrategy`** tries **guest HTTP** first:
   - Listing chunks from LinkedIn’s public guest endpoint (see §5).
   - For each job card, optional extra HTTP fetches for the **public job page** (description, JSON-LD salary, optional off-site apply link).
5. If guest HTTP yields **no usable listing** (empty first page, login-shaped HTML, hard failure), the strategy falls back to **Selenium + Chrome**: opens the search URL, walks the SERP, opens rows to fill the details pane, paginates with “show more” when needed.

### 2.2 Package layout (import map)

| Area | Main imports |
|------|----------------|
| Scraper entry | `from linkedin_jobs_scraper import LinkedinScraper` |
| Queries | `from linkedin_jobs_scraper.query import Query, QueryOptions, QueryFilters` |
| Filters | `from linkedin_jobs_scraper.filters import RelevanceFilters, TimeFilters, TypeFilters, …` |
| Events | `from linkedin_jobs_scraper.events import Events, EventData` |
| Exceptions | `from linkedin_jobs_scraper.exceptions import CallbackException` |

Supporting modules (usually no need to import): `linkedin_jobs_scraper.utils.guest_jobs_http`, `…utils.job_salary_ldjson`, `…utils.chrome_driver`, `…config`.

---

## 3. Requirements and installation

- **Python** ≥ 3.7  
- **Dependency:** `selenium>=4.12.0` (declared in `setup.py`).  
- **Chrome + matching ChromeDriver:** required when the **guest HTTP path fails** and Selenium fallback runs. Modern Selenium 4 often resolves the driver automatically; you can still pass `chrome_executable_path` / `chrome_binary_location` when needed.

**Install from PyPI:**

```bash
pip install linkedin-jobs-scraper
```

**Install from a local clone of this repository:**

```bash
cd /path/to/py-linkedin-jobs-scraper
pip install .
```

Optional: **`pandas`** (or any storage layer) if you aggregate `EventData` into tables—see §7.

---

## 4. `LinkedinScraper` — constructor and runtime

### 4.1 Constructor

```python
LinkedinScraper(
    chrome_executable_path: str = None,
    chrome_binary_location: str = None,
    chrome_options: selenium.webdriver.chrome.options.Options = None,
    headless: bool = True,
    max_workers: int = 2,
    slow_mo: float = 0.5,
    page_load_timeout: int = 20,
)
```

| Parameter | Meaning |
|------------|---------|
| `chrome_executable_path` | Path to the **chromedriver** binary (passed to `ChromeService`). Optional if Selenium can discover the driver. |
| `chrome_binary_location` | Path to **Chrome/Chromium** binary when it is not on the default `PATH`. |
| `chrome_options` | Full custom **`Options`** instance. If you pass this, **`headless` is ignored**; you own the full Chrome flags. |
| `headless` | Used only when `chrome_options` is `None`: selects headless mode in default options (`--headless=new`). |
| `max_workers` | Size of the internal **`ThreadPoolExecutor`**: **one thread per `Query`** in `run()`. Must be ≥ 1. |
| `slow_mo` | Seconds to sleep between guest listing pages and between many Selenium steps. Higher values reduce load and 429-style throttling. README suggests **≥ ~1.3** for reliability; default in code is **0.5**. |
| `page_load_timeout` | Selenium page-load timeout in seconds. |

**Validation:** non-string paths, invalid `chrome_options` type, `max_workers < 1`, or negative `slow_mo` raise **`ValueError`**.

The scraper **always** constructs **`GuestStrategy`**; there is no pluggable strategy switch in the public API.

### 4.2 `run(queries, options=None)`

```python
def run(self, queries: Union[Query, List[Query]], options: QueryOptions = None) -> None:
```

- **`queries`:** a single **`Query`** or a **`list`** of **`Query`**. Each query is validated (`Query.validate()`).
- **`options`:** optional “global” **`QueryOptions`** merged into every query via **`Query.merge_options()`** (see §6.3).

**Threading model:** each query is submitted to the pool with **`__run(query)`**, which loops locations **sequentially** for that query but **different queries** can execute **in parallel** up to `max_workers`.

**`Events.END`:** emitted once **per query** when that query’s location loop finishes (including after errors caught inside `__run`), not once globally after all futures complete.

### 4.3 Proxy-related methods on `LinkedinScraper`

The class exposes **`get_proxies`**, **`set_proxies`**, **`add_proxy`**, and **`remove_proxy`**. In the current guest implementation, **guest HTTP** uses **`urllib.request`** with fixed headers and **does not read** these lists; **Selenium** builds the driver via **`build_driver()`** without applying them either. Treat these methods as **legacy / unused** unless you fork the library. For proxies in practice, inject them through **`chrome_options`** (Selenium) or environment-level HTTP proxies for your own wrapper.

---

## 5. Guest HTTP layer (preferred path)

Implemented in **`linkedin_jobs_scraper.utils.guest_jobs_http`**.

- **Listing URL:** `https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search` with the same query string parameters as **`/jobs/search`** (see `JOBS_GUEST_SEE_MORE_URL` in `utils/constants.py`).
- **Pagination:** `start` is advanced by **`len(cards)`** from the last response, not always 25—because LinkedIn sometimes returns fewer than **`JOBS_PAGE_SIZE`** (25) cards per chunk.
- **Blocking heuristics:** `is_blocked_guest_html()` inspects a prefix of the HTML for auth wall / checkpoint patterns.
- **Cards:** `parse_guest_listing_cards()` regex-parses `data-entity-urn="urn:li:jobPosting:<id>"` blocks into **`GuestJobCard`** (id, link, title, company, place, date, raw HTML block).
- **Promoted jobs:** `is_promoted_guest_card()` checks the card block for “Promoted”; skipped when **`skip_promoted_jobs`** is true.
- **Job page:** `fetch_job_page_details(url, fetch_apply_link, slow_mo)` GETs the public job page, extracts description markup, optional **`data-is-offsite-apply`** apply URL, and **salary** via JSON-LD (`extract_salary_from_job_page_html`).

**User-Agent / headers:** default desktop Chrome-like headers are set in `guest_jobs_http`; there is no per-request hook exposed—fork or monkeypatch if you must change them.

---

## 6. Queries, options, and filters

### 6.1 `Query`

```python
Query(query: str = '', options: QueryOptions = QueryOptions())
```

- **`query`:** keyword string for `keywords=` in the search URL. **Empty string is allowed** if you rely on location-only or company URL filters (behavior depends on what LinkedIn returns for that combination).
- **`options`:** per-query **`QueryOptions`**.

**`validate()`:** ensures `query` is a `str` and delegates to `options.validate()`.

### 6.2 `QueryOptions`

```python
QueryOptions(
    limit: int = None,
    locations: List[str] = None,   # or a single str, coerced to one-element list
    filters: QueryFilters = None,
    apply_link: bool = None,
    skip_promoted_jobs: bool = None,
    page_offset: int = 0,
)
```

| Field | Role |
|-------|------|
| `limit` | Maximum jobs to collect **per location** for this query. After merge, default **25** if still unset. |
| `locations` | Human-readable location strings (e.g. `"United States"`, `"Remote"`). Passed as `location=` query param. |
| `filters` | Optional **`QueryFilters`** (§6.4). |
| `apply_link` | If true, each job triggers extra work to resolve **off-site apply** URL from HTML (`False` after merge by default). |
| `skip_promoted_jobs` | If true, guest HTTP skips cards detected as promoted (`False` after merge by default). |
| `page_offset` | Integer ≥ 0; shifts the starting SERP page before scraping (§6.5). |

**`validate()`:** `limit` must be a non-negative int; `locations` a list of strings; booleans for flags; `page_offset` a non-negative int; `filters` validated if present.

### 6.3 Merging global `options` in `run()`

If **`run(queries, options=None)`**, the scraper uses default global options:

```python
QueryOptions(locations=['Worldwide'], limit=25)
```

For each query, **`merge_options(global_options)`** applies:

- **`limit`:** if the query’s `limit` is `None`, set from global or **25**.
- **`apply_link`:** if `None`, set from global or **`False`**.
- **`skip_promoted_jobs`:** if `None`, set from global or **`False`**.
- **`locations`:** if the query’s `locations` is `None`, copy from global.
- **`filters`:** if the query’s `filters` is `None`, copy from global.

Per-field overrides on a specific **`Query`** therefore win when they are explicitly set.

### 6.4 `QueryFilters`

```python
QueryFilters(
    company_jobs_url: str = None,
    relevance: RelevanceFilters = None,
    time: TimeFilters = None,
    type: Union[TypeFilters, List[TypeFilters]] = None,
    experience: Union[ExperienceLevelFilters, List[ExperienceLevelFilters]] = None,
    on_site_or_remote: Union[OnSiteOrRemoteFilters, List[OnSiteOrRemoteFilters]] = None,
    base_salary: SalaryBaseFilters = None,
    industry: Union[IndustryFilters, List[IndustryFilters]] = None,
)
```

**URL mapping** (built in `LinkedinScraper.__build_search_url`):

| Filter | URL parameter | Notes |
|--------|----------------|--------|
| Company | `f_C` | Extracted from **`company_jobs_url`** (must contain `f_C`). |
| Relevance | `sortBy` | `R` vs `DD` (see enums). |
| Time | `f_TPR` | LinkedIn time window token. |
| Base salary | `f_SB2` | Discrete buckets. |
| Job type | `f_JT` | Comma-joined enum **values**. |
| Experience | `f_E` | Comma-joined. |
| Industry | `f_I` | Comma-joined. |
| On-site / remote | `f_WT` | Comma-joined. |

**`validate()`:** validates `company_jobs_url` (must be parseable and include **`f_C`**), and types of relevance, time, base_salary, type, experience, and on_site_or_remote. **`industry`** is **not** type-checked against `IndustryFilters` in `validate()`—incorrect types may fail later or produce odd URLs.

**Guest caveat:** LinkedIn’s guest listing may **not honor every filter combination** the same way the logged-in site does. Always verify a sample of URLs and results.

### 6.5 `page_offset` and `start`

`GuestStrategy.run()` applies:

```text
start = page_offset * JOBS_PAGE_SIZE   # JOBS_PAGE_SIZE == 25
```

via `override_query_params` on the search URL before guest HTTP or Selenium. So **`page_offset=1`** skips the first **25** slots of the ranked list (subject to how LinkedIn interprets `start`).

---

## 7. Filter enums (`linkedin_jobs_scraper.filters`)

Values are LinkedIn’s internal codes unless noted.

**`RelevanceFilters`**

- `RELEVANT` → `'R'`
- `RECENT` → `'DD'` (passed as `sortBy`)

**`TimeFilters`** (`f_TPR`)

- `ANY` → `''`
- `DAY` → `'r86400'`
- `WEEK` → `'r604800'`
- `MONTH` → `'r2592000'`

**`TypeFilters`** (`f_JT`, combinable)

- `FULL_TIME` `'F'`, `PART_TIME` `'P'`, `TEMPORARY` `'T'`, `CONTRACT` `'C'`, `INTERNSHIP` `'I'`, `VOLUNTEER` `'V'`, `OTHER` `'O'`

**`ExperienceLevelFilters`** (`f_E`)

- `INTERNSHIP` `'1'` … `EXECUTIVE` `'6'` (see `filters.py` for full list)

**`OnSiteOrRemoteFilters`** (`f_WT`)

- `ON_SITE` `'1'`, `REMOTE` `'2'`, `HYBRID` `'3'`

**`SalaryBaseFilters`** (`f_SB2`)

- `'1'` … `'9'` — mapped to increasing salary floors (`SALARY_40K` … `SALARY_200K`)

**`IndustryFilters`** (`f_I`)

- A **fixed subset** of industries (software, banking, etc.). To support a code LinkedIn uses but the enum lacks, **add a member** in `linkedin_jobs_scraper/filters/filters.py` following the same pattern, or pass the numeric code only if you extend validation accordingly.

**Company jobs URL**

- Copy from the browser: company page → Jobs → “See all jobs” (or equivalent). The URL must include **`f_C=…`** (company entity id). Paste the full URL into **`QueryFilters(company_jobs_url="…")`**.

---

## 8. Events API

### 8.1 Registration

```python
scraper.on(Events.DATA, callback, once=False)
scraper.once(Events.DATA, callback)   # equivalent to once=True
```

**Rules:**

- **`Events.DATA`**, **`Events.ERROR`**, **`Events.METRICS`:** callback must be a **plain function** (`types.FunctionType`) with **exactly one** parameter.
- **`Events.END`:** callback must have **zero** parameters.
- Violations → **`ValueError`**.

**Removal:**

```python
scraper.remove_listener(Events.DATA, callback)  # returns bool
scraper.remove_all_listeners(Events.DATA)
```

**Errors inside callbacks:** any exception from a listener is wrapped in **`CallbackException`** and propagates (can abort the run).

### 8.2 `Events` enum

| Member | Payload |
|--------|---------|
| `DATA` | **`EventData`** |
| `ERROR` | `str` (message, often with traceback) |
| `METRICS` | *Reserved in API; guest strategy path in this version does not emit `METRICS`.* |
| `END` | none |

### 8.3 `EventData` (NamedTuple)

All fields have defaults; guest mode **typically fills** a subset.

| Field | Typical guest HTTP | Notes |
|-------|-------------------|--------|
| `query` | ✓ | Original keyword string |
| `location` | ✓ | Location string for this pipeline |
| `job_id` | ✓ | Parsed posting id |
| `job_index` | ✓ | 0-based index (debug / ordering) |
| `link` | ✓ | Public job URL |
| `apply_link` | If `apply_link=True` | Off-site apply URL when present in HTML |
| `title`, `company`, `place` | ✓ | From card / DOM |
| `date` | ✓ | ISO datetime from `<time datetime>` when present |
| `description` | ✓ | Plain text from description markup |
| `description_html` | ✓ | HTML fragment when extracted |
| `salary` | Sometimes | From **JSON-LD** `JobPosting` salary fields when LinkedIn includes them; else `''` |
| `date_text` | Often empty | Human-readable date line |
| `company_link`, `company_img_link` | Often empty | Not populated on guest paths |
| `insights`, `skills` | Default `[]` | Not populated on guest paths |

Because **`EventData`** is a **`NamedTuple`**, treat it as immutable when reasoning about pipelines.

---

## 9. Logging

- Logger namespace: **`li:scraper`** (`Config.LOGGER_NAMESPACE` in `linkedin_jobs_scraper/config.py`).
- Default level: **`INFO`**, unless **`LOG_LEVEL`** env var is set to one of: **`DEBUG`**, **`INFO`**, **`WARN`/`WARNING`**, **`ERROR`**, **`FATAL`**.

```python
import logging
logging.getLogger("li:scraper").setLevel(logging.DEBUG)
```

---

## 10. End-to-end examples

### 10.1 Minimal callback scraper

```python
import logging
from linkedin_jobs_scraper import LinkedinScraper
from linkedin_jobs_scraper.events import Events, EventData
from linkedin_jobs_scraper.query import Query, QueryOptions, QueryFilters
from linkedin_jobs_scraper.filters import RelevanceFilters, TimeFilters, TypeFilters

logging.basicConfig(level=logging.INFO)


def on_data(data: EventData) -> None:
    print(data.title, "|", data.company, "|", data.place, "|", data.link)


def on_error(err: str) -> None:
    print("ERROR:", err)


def on_end() -> None:
    print("Batch finished.")


scraper = LinkedinScraper(headless=True, max_workers=1, slow_mo=1.3)
scraper.on(Events.DATA, on_data)
scraper.on(Events.ERROR, on_error)
scraper.on(Events.END, on_end)

queries = [
    Query(
        query="Python developer",
        options=QueryOptions(
            locations=["United States"],
            limit=10,
            filters=QueryFilters(
                relevance=RelevanceFilters.RECENT,
                time=TimeFilters.WEEK,
                type=[TypeFilters.FULL_TIME],
            ),
        ),
    ),
]

scraper.run(queries)
```

### 10.2 Global defaults + per-query override

```python
scraper.run(
    [
        Query("Data engineer", QueryOptions()),  # inherits locations + limit from global
        Query(
            "ML engineer",
            QueryOptions(locations=["Canada"], limit=5),  # overrides location + limit
        ),
    ],
    QueryOptions(locations=["Germany", "Netherlands"], limit=15),
)
```

### 10.3 Collecting into a `pandas.DataFrame`

```python
import logging
from typing import List, Dict, Any

import pandas as pd

from linkedin_jobs_scraper import LinkedinScraper
from linkedin_jobs_scraper.events import Events, EventData
from linkedin_jobs_scraper.query import Query, QueryOptions, QueryFilters
from linkedin_jobs_scraper.filters import RelevanceFilters, TimeFilters, TypeFilters

logging.basicConfig(level=logging.INFO)


def scrape_to_dataframe(queries: List[Query]) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []

    def on_data(data: EventData) -> None:
        rows.append(
            {
                "title": data.title,
                "location_on_post": data.place,
                "listed_at": data.date,
                "listed_at_text": data.date_text,
                "description": data.description,
                "salary_structured": data.salary or None,
                "company": data.company,
                "link": data.link,
                "job_id": data.job_id,
                "apply_link": data.apply_link or None,
            }
        )

    scraper = LinkedinScraper(headless=True, max_workers=1, slow_mo=1.3)
    scraper.on(Events.DATA, on_data)
    scraper.on(Events.ERROR, lambda e: logging.warning("%s", e))
    scraper.run(queries)
    return pd.DataFrame(rows)


df = scrape_to_dataframe(
    [
        Query(
            query="Site reliability engineer",
            options=QueryOptions(
                locations=["Remote"],
                limit=20,
                apply_link=True,
                skip_promoted_jobs=True,
                page_offset=0,
                filters=QueryFilters(
                    relevance=RelevanceFilters.RECENT,
                    time=TimeFilters.MONTH,
                    type=[TypeFilters.FULL_TIME],
                ),
            ),
        )
    ]
)
print(df.head())
```

### 10.4 Custom Chrome options (non-headless, custom binary, extensions)

If you pass **`chrome_options`**, you must configure headless (or not) yourself:

```python
from selenium.webdriver.chrome.options import Options
from linkedin_jobs_scraper import LinkedinScraper
from linkedin_jobs_scraper.query import Query, QueryOptions

opts = Options()
# opts.add_argument("--headless=new")  # optional
opts.binary_location = "/usr/bin/chromium"

scraper = LinkedinScraper(chrome_options=opts, max_workers=1, slow_mo=1.5)
scraper.run([Query("DevOps", QueryOptions(locations=["France"], limit=3))])
```

---

## 11. Operational guidance

### 11.1 Rate limiting and politeness

- Prefer **`max_workers=1`** unless you understand the extra load on LinkedIn and your network.
- Increase **`slow_mo`** when you see empty results, HTTP errors, or frequent fallback to Selenium.
- **`apply_link=True`** and high **`limit`** multiply HTTP requests—scale **`slow_mo`** up accordingly.

### 11.2 When Selenium runs

Selenium starts when guest HTTP **does not** complete the query successfully—e.g. first listing empty, blocked HTML, or request errors before any job is emitted. Ensure **Chrome** is installed; match **ChromeDriver** to Chrome major version where manual `executable_path` is used.

### 11.3 Common failure modes

| Symptom | Likely cause | Mitigation |
|---------|----------------|------------|
| `ERROR` about Chrome / driver | Driver or browser missing / wrong version | Install or pin versions; set paths |
| Login / checkpoint message | IP or rate flagged | Back off, change network, increase `slow_mo`, reduce workers |
| Fewer jobs than `limit` | End of results, filters, promoted skips, or parse miss | Widen query, check filters, inspect HTML changes |
| Empty `salary` | No JSON-LD salary on page | Normal; pay may only appear in description text |

### 11.4 Tests in this repository

- Fast tests: **`pytest -m "not integration"`** (see `pytest.ini`).
- Integration tests hit real LinkedIn + Chrome: **`pytest tests/test_.py`** (marked `integration`).

---

## 12. Exceptions

- **`CallbackException`** (`linkedin_jobs_scraper.exceptions`): raised when an event callback throws; message includes the original traceback.
- **`ValueError`:** invalid constructor or `run()` / `on()` arguments, or invalid query / filter configuration.

---

## 13. Version and upstream

Package version is defined in **`setup.py`** (e.g. `5.0.2` at time of writing). For bug reports and upstream source, see the URL in **`setup.py`** (`https://github.com/spinlud/py-linkedin-jobs-scraper.git`).

---

## 14. License

The project is distributed under the **MIT License** (see repository `LICENSE` or PyPI metadata).
