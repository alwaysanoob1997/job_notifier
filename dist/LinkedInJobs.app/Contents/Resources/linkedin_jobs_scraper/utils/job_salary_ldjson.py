"""
Extract pay range from LinkedIn public job HTML when present in JSON-LD JobPosting.
"""
from __future__ import annotations

import json
import re
from typing import Any, List


def extract_salary_from_job_page_html(html: str) -> str:
    """Return a single human-readable salary string, or '' if none found."""
    if not html:
        return ''
    for m in re.finditer(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>([\s\S]*?)</script>',
        html,
        re.IGNORECASE,
    ):
        raw = m.group(1).strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        items: List[Any] = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            types = item.get('@type')
            is_job = types == 'JobPosting' or (
                isinstance(types, list) and 'JobPosting' in types
            )
            if not is_job:
                continue
            for key in ('baseSalary', 'estimatedSalary'):
                s = _salary_value_to_str(item.get(key))
                if s:
                    return s
    return ''


def _salary_value_to_str(val: Any) -> str:
    if val is None:
        return ''
    if isinstance(val, str):
        return val.strip()
    if isinstance(val, dict):
        return _monetary_or_value_dict(val)
    if isinstance(val, list):
        parts = [_salary_value_to_str(x) for x in val]
        return ' / '.join(p for p in parts if p)
    return ''


def _quantitative_value_to_str(v: dict, currency: str = '') -> str:
    lo = v.get('minValue')
    hi = v.get('maxValue')
    unit = (v.get('unitText') or '').replace('_', ' ').strip()
    cur = (v.get('currency') or currency or '').strip()
    val = v.get('value')
    if lo is not None and hi is not None:
        body = f'{lo}–{hi}' if lo != hi else str(lo)
    elif lo is not None:
        body = str(lo)
    elif hi is not None:
        body = str(hi)
    elif val is not None:
        body = str(val)
    else:
        return ''
    parts = [body]
    if unit:
        parts.append(unit)
    if cur:
        parts.append(cur)
    return ' '.join(parts)


def _monetary_or_value_dict(obj: dict) -> str:
    t = obj.get('@type')
    currency = (obj.get('currency') or '').strip()

    if t == 'MonetaryAmount' or 'value' in obj:
        inner = obj.get('value')
        if isinstance(inner, dict):
            return _quantitative_value_to_str(inner, currency)
        if isinstance(inner, (int, float)):
            return _join_amount_currency(str(inner), currency)

    if t == 'QuantitativeValue' or 'minValue' in obj or 'maxValue' in obj:
        return _quantitative_value_to_str(obj, currency)

    if isinstance(obj.get('value'), (int, float)):
        return _join_amount_currency(str(obj['value']), currency)
    return ''


def _join_amount_currency(amount: str, currency: str) -> str:
    return f'{amount} {currency}'.strip() if currency else amount
