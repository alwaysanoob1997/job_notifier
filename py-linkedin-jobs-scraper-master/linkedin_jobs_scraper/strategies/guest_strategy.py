import traceback
from typing import List, NamedTuple, Optional, TYPE_CHECKING

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as ec
from time import sleep

from .strategy import Strategy
from ..query import Query
from ..utils.chrome_driver import build_driver
from ..utils.logger import debug, info, warn, error
from ..utils.url import override_query_params
from ..utils.constants import JOBS_PAGE_SIZE
from ..utils.job_salary_ldjson import extract_salary_from_job_page_html
from ..utils.guest_jobs_http import (
    fetch_guest_listing_html,
    fetch_job_page_details,
    is_blocked_guest_html,
    is_login_challenge_url,
    is_promoted_guest_card,
    jobs_search_params_to_guest_query,
    parse_guest_listing_cards,
)
from ..events import Events, EventData

if TYPE_CHECKING:
    from ..linkedin_scraper import LinkedinScraper


class GuestSerpSelectors(NamedTuple):
    container: str
    jobs: str
    links: str
    companies: str
    places: str
    dates: str
    details_panel: str
    description: str
    see_more_jobs: str


# Legacy two-pane guest SERP vs newer base-card list.
SELECTOR_VARIANTS: List[GuestSerpSelectors] = [
    GuestSerpSelectors(
        container='.results__container.results__container--two-pane',
        jobs='.jobs-search__results-list li',
        links='.jobs-search__results-list li a.result-card__full-card-link',
        companies='.result-card__subtitle.job-result-card__subtitle',
        places='.job-result-card__location',
        dates='time',
        details_panel='.details-pane__content, .jobs-details__main-content',
        description='.description__text, .show-more-less-html__markup',
        see_more_jobs='button.infinite-scroller__show-more-button',
    ),
    GuestSerpSelectors(
        container='.two-pane-serp-page__results-list',
        jobs='.jobs-search__results-list li',
        links='a.base-card__full-link',
        companies='.base-search-card__subtitle',
        places='.job-search-card__location',
        dates='time',
        details_panel='.details-pane__content, .jobs-details__main-content',
        description='.description__text, .show-more-less-html__markup',
        see_more_jobs='button.infinite-scroller__show-more-button',
    ),
]

# Extra containers seen on some guest / hybrid layouts (try in order after variants).
FALLBACK_CONTAINERS: List[str] = [
    '.jobs-search-results__list',
    '.jobs-search-content__list',
    'ul.jobs-search__results-list',
]


class GuestStrategy(Strategy):
    def __init__(self, scraper: 'LinkedinScraper'):
        super().__init__(scraper)

    @staticmethod
    def __blocked_login_or_challenge(driver: webdriver) -> bool:
        try:
            title = driver.title or ''
        except BaseException:
            title = ''
        return is_login_challenge_url(driver.current_url, title)

    def __load_job_details(
        self,
        driver: webdriver,
        selectors: GuestSerpSelectors,
        job_id: str,
        timeout: Optional[float] = None,
    ) -> object:
        if timeout is None:
            timeout = self.scraper.job_details_wait_timeout
        elapsed = 0.0
        sleep_time = 0.05

        while elapsed < timeout:
            loaded = driver.execute_script(
                '''
                    const jobId = (arguments[0] || '').trim();
                    const detailsPanel = document.querySelector(arguments[1]);
                    const description = document.querySelector(arguments[2]);
                    if (!detailsPanel || !description || description.innerText.trim().length === 0) {
                        return false;
                    }
                    if (!jobId) {
                        return true;
                    }
                    return detailsPanel.innerHTML.includes(jobId);
                ''',
                job_id,
                selectors.details_panel,
                selectors.description,
            )

            if loaded:
                return {'success': True}

            sleep(sleep_time)
            elapsed += sleep_time

        return {'success': False, 'error': 'Timeout on loading job details'}

    def __load_more_jobs(
        self,
        driver: webdriver,
        selectors: GuestSerpSelectors,
        job_links_tot: int,
        timeout: Optional[float] = None,
    ) -> object:
        if timeout is None:
            timeout = self.scraper.job_details_wait_timeout
        elapsed = 0.0
        sleep_time = 0.05
        clicked = False

        while elapsed < timeout:
            if not clicked:
                clicked = driver.execute_script(
                    '''
                        const button = document.querySelector(arguments[0]);
                        if (button) {
                            button.click();
                            return true;
                        }
                        return false;
                    ''',
                    selectors.see_more_jobs,
                )

            loaded = driver.execute_script(
                '''
                    window.scrollTo(0, document.body.scrollHeight);
                    return document.querySelectorAll(arguments[0]).length > arguments[1];
                ''',
                selectors.jobs,
                job_links_tot,
            )

            if loaded:
                return {'success': True}

            sleep(sleep_time)
            elapsed += sleep_time

        return {'success': False, 'error': 'Timeout on loading more jobs'}

    @staticmethod
    def __accept_cookies(driver: webdriver, tag: str) -> None:
        try:
            driver.execute_script(
                r'''
                    const buttons = Array.from(document.querySelectorAll('button'));
                    const labels = [
                        'Accept cookies',
                        'Accept all cookies',
                        'Reject optional cookies',
                        'Accept',
                    ];
                    for (const label of labels) {
                        const b = buttons.find(e =>
                            (e.innerText || '').trim().includes(label));
                        if (b) {
                            b.click();
                            break;
                        }
                    }
                '''
            )
        except BaseException:
            debug(tag, 'Failed to accept cookies')

    def __try_guest_http(
        self,
        search_url: str,
        query: Query,
        location: str,
    ) -> bool:
        """
        Fetch jobs via public guest seeMoreJobPostings API + public job view pages.
        Returns True if no Selenium fallback is needed (guest path handled the query).
        """
        limit = query.options.limit if query.options.limit is not None else 25
        apply_fetch = bool(query.options.apply_link)
        skip_promoted = bool(query.options.skip_promoted_jobs)

        params = jobs_search_params_to_guest_query(search_url)
        try:
            start = int(params.get('start', 0) or 0)
        except ValueError:
            start = 0

        processed = 0
        job_index_global = 0
        first_listing = True

        while processed < limit:
            try:
                listing_html = fetch_guest_listing_html(params, start)
            except BaseException as e:
                warn(f'[{query.query}][{location}]', 'Guest HTTP listing failed', e)
                return processed > 0

            if is_blocked_guest_html(listing_html):
                warn(f'[{query.query}][{location}]', 'Guest listing HTML looks like login wall; trying browser fallback')
                return processed > 0

            cards = parse_guest_listing_cards(listing_html)
            if not cards:
                if first_listing:
                    return False
                break

            first_listing = False

            for card in cards:
                if processed >= limit:
                    break
                if skip_promoted and is_promoted_guest_card(card.raw_block):
                    continue

                try:
                    desc_text, desc_html, apply_link, salary = fetch_job_page_details(
                        card.link,
                        apply_fetch,
                        self.scraper.slow_mo,
                    )
                except BaseException as e:
                    error(f'[{query.query}][{location}]', 'Guest job page fetch failed', e, exc_info=False)
                    job_index_global += 1
                    continue

                data = EventData(
                    query=query.query,
                    location=location,
                    job_id=card.job_id,
                    job_index=job_index_global,
                    title=card.title,
                    company=card.company,
                    place=card.place,
                    date=card.date,
                    link=card.link,
                    apply_link=apply_link or '',
                    description=desc_text,
                    description_html=desc_html or '',
                    salary=salary or '',
                )
                self.scraper.emit(Events.DATA, data)
                processed += 1
                job_index_global += 1

            if processed >= limit:
                break

            # Guest seeMoreJobPostings often returns < JOBS_PAGE_SIZE (e.g. 10) per chunk;
            # advance by returned card count so remaining results are not skipped.
            start += len(cards)
            sleep(self.scraper.slow_mo)

        return processed > 0

    def __pick_serp_selectors(self, driver: webdriver, tag: str) -> Optional[GuestSerpSelectors]:
        for variant in SELECTOR_VARIANTS:
            try:
                info(tag, f'Waiting for SERP container {variant.container}')
                WebDriverWait(driver, 5).until(
                    ec.presence_of_element_located((By.CSS_SELECTOR, variant.container))
                )
                return variant
            except BaseException:
                continue

        for extra in FALLBACK_CONTAINERS:
            try:
                info(tag, f'Trying fallback container {extra}')
                WebDriverWait(driver, 4).until(
                    ec.presence_of_element_located((By.CSS_SELECTOR, extra))
                )
                # Use newest card selectors with generic list items under jobs list
                return GuestSerpSelectors(
                    container=extra,
                    jobs='.jobs-search__results-list li, .jobs-search-results__list-item',
                    links='a.base-card__full-link, a.result-card__full-card-link',
                    companies='.base-search-card__subtitle, .result-card__subtitle',
                    places='.job-search-card__location, .job-result-card__location',
                    dates='time',
                    details_panel='.details-pane__content, .jobs-details__main-content',
                    description='.description__text, .show-more-less-html__markup',
                    see_more_jobs='button.infinite-scroller__show-more-button',
                )
            except BaseException:
                continue

        return None

    def run(
        self,
        driver: Optional[webdriver],
        search_url: str,
        query: Query,
        location: str,
        page_offset: int,
    ) -> None:
        search_url = override_query_params(
            search_url,
            {'start': str(page_offset * JOBS_PAGE_SIZE)},
        )

        tag = f'[{query.query}][{location}]'

        if self.__try_guest_http(search_url, query, location):
            info(tag, 'Guest run completed via public guest HTTP')
            return

        own_driver = False
        if driver is None:
            try:
                driver = build_driver(
                    executable_path=self.scraper.chrome_executable_path,
                    binary_location=self.scraper.chrome_binary_location,
                    options=self.scraper.chrome_options,
                    headless=self.scraper.headless,
                    timeout=self.scraper.page_load_timeout,
                )
                own_driver = True
            except BaseException as e:
                msg = (
                    'Guest mode: public guest HTTP returned no jobs and Chrome could not be started '
                    'for Selenium fallback. Install Chrome/Chromedriver, or check keywords/location.'
                )
                error(tag, msg, e, exc_info=False)
                self.scraper.emit(Events.ERROR, f'{msg}\n{e!s}')
                return

        try:
            processed = 0
            info(tag, f'Opening {search_url} (Selenium fallback)')
            driver.get(search_url)

            if self.__blocked_login_or_challenge(driver):
                msg = (
                    'LinkedIn redirected to a login or verification page in guest mode. '
                    'Try again later, reduce rate, or use a different network.'
                )
                error(tag, msg, exc_info=False)
                self.scraper.emit(Events.ERROR, msg)
                return

            selectors = self.__pick_serp_selectors(driver, tag)
            if selectors is None:
                msg = 'Could not find jobs search layout (SERP). Page structure may have changed.'
                warn(tag, msg)
                self.scraper.emit(Events.ERROR, msg)
                return

            job_index = 0
            info(tag, 'OK (Selenium); starting pagination loop')

            apply_link_selector = 'a[data-is-offsite-apply=true]'

            while processed < (query.options.limit or 25):
                self.__accept_cookies(driver, tag)

                jobs_tot = driver.execute_script(
                    'return document.querySelectorAll(arguments[0]).length;',
                    selectors.jobs,
                )

                if jobs_tot == 0:
                    info(tag, 'No jobs found, skip')
                    break

                info(tag, f'Found {jobs_tot} jobs')

                while job_index < jobs_tot and processed < (query.options.limit or 25):
                    sleep(self.scraper.slow_mo)
                    jtag = f'[{query.query}][{location}][{processed + 1}]'

                    debug(
                        jtag,
                        'Evaluating selectors',
                        [
                            selectors.jobs,
                            selectors.links,
                            selectors.companies,
                            selectors.places,
                            selectors.dates,
                        ],
                    )

                    try:
                        row = driver.execute_script(
                            '''
                            const index = arguments[0];
                            const jobsSel = arguments[1];
                            const linkSel = arguments[2];
                            const companySel = arguments[3];
                            const placeSel = arguments[4];
                            const dateSel = arguments[5];

                            const job = document.querySelectorAll(jobsSel)[index];
                            if (!job) return null;

                            const link = job.querySelector(linkSel);
                            if (!link) return null;

                            link.scrollIntoView();
                            link.click();
                            const linkUrl = link.getAttribute('href') || '';

                            let jobId = job.getAttribute('data-job-id')
                                || job.getAttribute('data-id')
                                || '';

                            if (!jobId) {
                                let el = link;
                                for (let depth = 0; depth < 6 && el; depth++) {
                                    const urn = el.getAttribute && el.getAttribute('data-entity-urn');
                                    if (urn && urn.indexOf('jobPosting:') >= 0) {
                                        jobId = urn.split('jobPosting:').pop().split(':')[0];
                                        break;
                                    }
                                    el = el.parentElement;
                                }
                            }
                            if (!jobId) {
                                const m = (linkUrl || '').match(/view\\/[^?]*?(\\d{6,})/)
                                    || (linkUrl || '').match(/currentJobId=(\\d+)/);
                                if (m) jobId = m[1];
                            }

                            const titleEl = link.querySelector('span') || link;
                            const titleText = (titleEl && titleEl.innerText) ? titleEl.innerText.trim()
                                : (link.innerText || '').trim();

                            const companyEl = job.querySelector(companySel);
                            const placeEl = job.querySelector(placeSel);
                            const timeEl = job.querySelector(dateSel);

                            return [
                                jobId || '',
                                linkUrl,
                                titleText,
                                companyEl ? companyEl.innerText.trim() : '',
                                placeEl ? placeEl.innerText.trim() : '',
                                timeEl ? (timeEl.getAttribute('datetime') || timeEl.innerText.trim() || '') : '',
                            ];
                            ''',
                            job_index,
                            selectors.jobs,
                            selectors.links,
                            selectors.companies,
                            selectors.places,
                            selectors.dates,
                        )

                        if not row:
                            warn(jtag, 'Skipping row: no job/link at index')
                            job_index += 1
                            continue

                        job_id, job_link, job_title, job_company, job_place, job_date = row

                        debug(jtag, f'Loading details of job {job_id}')
                        load_result = self.__load_job_details(driver, selectors, job_id)

                        if not load_result['success']:
                            error(jtag, load_result['error'])
                            job_index += 1
                            continue

                        debug(jtag, 'Evaluating description', [selectors.description])
                        job_description, job_description_html = driver.execute_script(
                            '''
                            const el = document.querySelector(arguments[0]);
                            if (!el) return ['', ''];
                            return [el.innerText || '', el.outerHTML || ''];
                            ''',
                            selectors.description,
                        )

                        debug(jtag, 'Evaluating apply link', [apply_link_selector])
                        job_apply_link = driver.execute_script(
                            '''
                            const applyBtn = document.querySelector(arguments[0]);
                            return applyBtn ? applyBtn.getAttribute('href') || '' : '';
                            ''',
                            apply_link_selector,
                        )

                        job_salary = extract_salary_from_job_page_html(driver.page_source)

                    except BaseException as e:
                        error(jtag, e, traceback.format_exc())
                        self.scraper.emit(Events.ERROR, str(e) + '\n' + traceback.format_exc())
                        job_index += 1
                        continue

                    data = EventData(
                        query=query.query,
                        location=location,
                        job_id=job_id,
                        job_index=job_index,
                        title=job_title,
                        company=job_company,
                        place=job_place,
                        date=job_date,
                        link=job_link,
                        apply_link=job_apply_link,
                        description=job_description,
                        description_html=job_description_html,
                        salary=job_salary,
                    )

                    info(jtag, 'Processed')
                    job_index += 1
                    processed += 1
                    self.scraper.emit(Events.DATA, data)

                    if processed < (query.options.limit or 25) and job_index == jobs_tot:
                        jobs_tot = driver.execute_script(
                            'return document.querySelectorAll(arguments[0]).length;',
                            selectors.jobs,
                        )

                if processed == (query.options.limit or 25):
                    break

                info(tag, 'Checking for new jobs to load...')
                load_result = self.__load_more_jobs(driver, selectors, jobs_tot)

                if not load_result['success']:
                    info(tag, "Couldn't find more jobs for the running query")
                    break
        finally:
            if own_driver and driver is not None:
                try:
                    driver.quit()
                except BaseException:
                    pass
