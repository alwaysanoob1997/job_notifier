"""
Public guest jobs listing + job view pages (no li_at cookie).
Uses stdlib only; same data visible to non-logged-in visitors.
"""
from __future__ import annotations

import html as html_module
import re
import time
from typing import List, NamedTuple, Optional, Tuple
from urllib.parse import urlencode, urlparse, parse_qsl
from urllib.request import Request, urlopen

from .constants import JOBS_GUEST_SEE_MORE_URL, JOBS_PAGE_SIZE
from .job_salary_ldjson import extract_salary_from_job_page_html

def is_login_challenge_url(url: str, page_title: str = '') -> bool:
    """True when URL or title indicates LinkedIn login / checkpoint instead of jobs SERP."""
    path = urlparse(url).path.lower()
    needles = ('authwall', 'uas/login', '/login', 'checkpoint/lg', 'checkpoint', '/signup')
    if any(n in path for n in needles):
        return True
    t = (page_title or '').lower()
    if 'sign in' in t and 'linkedin' in t:
        return True
    if 'join linkedin' in t:
        return True
    return False


DEFAULT_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    ),
    'Accept-Language': 'en-US,en;q=0.9',
}


class GuestJobCard(NamedTuple):
    job_id: str
    link: str
    title: str
    company: str
    place: str
    date: str
    raw_block: str


def _http_get(url: str, timeout: int = 25) -> str:
    req = Request(url, headers=dict(DEFAULT_HEADERS), method='GET')
    with urlopen(req, timeout=timeout) as resp:
        return resp.read().decode('utf-8', errors='replace')


def jobs_search_params_to_guest_query(search_url: str) -> dict:
    """Merge query params from /jobs/search URL into a dict for guest API."""
    parsed = urlparse(search_url)
    return dict(parse_qsl(parsed.query, keep_blank_values=True))


def fetch_guest_listing_html(params: dict, start: int) -> str:
    p = dict(params)
    p['start'] = str(start)
    q = urlencode(p)
    url = f'{JOBS_GUEST_SEE_MORE_URL}?{q}'
    return _http_get(url)


def is_blocked_guest_html(fragment: str) -> bool:
    """Heuristic: guest listing replaced by login / challenge HTML."""
    sample = fragment[:12000].lower()
    if 'authwall' in sample or 'checkpoint' in sample:
        return True
    if 'sign in' in sample and 'linkedin' in sample and 'jobposting' not in sample:
        return True
    return False


def parse_guest_listing_cards(html: str) -> List[GuestJobCard]:
    """Parse HTML fragment from seeMoreJobPostings into job cards."""
    cards: List[GuestJobCard] = []
    pattern = re.compile(r'data-entity-urn="urn:li:jobPosting:(\d+)"')
    matches = list(pattern.finditer(html))
    for idx, m in enumerate(matches):
        job_id = m.group(1)
        block_start = m.start()
        block_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(html)
        block = html[block_start:block_end]

        link_m = re.search(r'<a class="base-card__full-link[^"]*" href="([^"]+)"', block)
        if not link_m:
            continue
        link = html_module.unescape(link_m.group(1))

        title_m = re.search(
            r'<h3 class="base-search-card__title">\s*([^<]+?)\s*</h3>',
            block,
            re.DOTALL,
        )
        title = html_module.unescape(title_m.group(1).strip()) if title_m else ''

        company_m = re.search(
            r'<h4 class="base-search-card__subtitle">[\s\S]*?<a[^>]*>([^<]+)</a>',
            block,
        )
        company = html_module.unescape(company_m.group(1).strip()) if company_m else ''

        place_m = re.search(
            r'class="job-search-card__location">\s*([^<]+?)\s*</span>',
            block,
        )
        place = html_module.unescape(place_m.group(1).strip()) if place_m else ''

        date_m = re.search(
            r'<time class="job-search-card__listdate"[^>]*datetime="([^"]*)"',
            block,
        )
        date = date_m.group(1) if date_m else ''

        cards.append(
            GuestJobCard(
                job_id=job_id,
                link=link,
                title=title,
                company=company,
                place=place,
                date=date,
                raw_block=block,
            )
        )
    return cards


def _strip_tags_to_text(fragment: str) -> str:
    t = re.sub(r'<[^>]+>', ' ', fragment)
    t = html_module.unescape(t)
    return re.sub(r'\s+', ' ', t).strip()


def fetch_job_page_details(
    view_url: str,
    fetch_apply_link: bool,
    slow_mo: float = 0.0,
) -> Tuple[str, str, str, str]:
    """
    Fetch public job view page; return (description_text, description_html, apply_link, salary).
    ``salary`` is parsed from JSON-LD when LinkedIn exposes it; otherwise ''.
    """
    if slow_mo:
        time.sleep(slow_mo)
    page = _http_get(view_url)
    desc_html = ''
    text = ''

    key = 'show-more-less-html__markup'
    i = page.find(key)
    if i != -1:
        frag = page[i:]
        gt = frag.find('>')
        if gt != -1:
            start = i + gt + 1
            end = page.find('show-more-less-html__button', start)
            if end == -1:
                end = page.find('</section>', start)
            if end == -1:
                end = start + min(50000, len(page) - start)
            desc_html = page[start:end].strip()
            text = _strip_tags_to_text(desc_html)

    if not text:
        m = re.search(
            r'show-more-less-html__markup[^>]*>([\s\S]*?)(?=show-more-less-html__button|</section>)',
            page,
            re.IGNORECASE,
        )
        if m:
            desc_html = m.group(1).strip()
            text = _strip_tags_to_text(desc_html)

    salary = extract_salary_from_job_page_html(page)

    apply_link = ''
    if fetch_apply_link:
        am = re.search(
            r'<a[^>]+data-is-offsite-apply="true"[^>]+href="([^"]+)"',
            page,
        )
        if am:
            apply_link = html_module.unescape(am.group(1))
        else:
            am2 = re.search(
                r'href="([^"]+)"[^>]*data-is-offsite-apply="true"',
                page,
            )
            if am2:
                apply_link = html_module.unescape(am2.group(1))

    return text, desc_html, apply_link, salary


def is_promoted_guest_card(block: str) -> bool:
    return 'Promoted' in block or 'promoted' in block.lower()
