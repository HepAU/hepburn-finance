"""Subscription audit.

Detects recurring small-amount payees by analysing transaction cadence over
the last 90 days. Anything with at least 2 hits, fairly consistent amounts,
and a roughly weekly / fortnightly / monthly cadence is flagged as a
subscription-like recurring charge. Total monthly bleed is reported, plus
which ones look low-utilisation (only 1 hit in 60+ days).
"""
import re
from datetime import date, timedelta
from collections import defaultdict

from app.database import get_db


# Heuristic: subscription-like = small recurring amount
SUBSCRIPTION_AMOUNT_MAX = 80.0
LOOKBACK_DAYS = 90


def _normalise_payee(description):
    """Reduce a transaction description to a stable merchant key.

    Strips trailing reference numbers, locations, and variation digits so
    'AMZNPRIMEAU* AMZNP,SYDNEY SOUTH' and 'AMZNPRIMEAU* AMZNP,MELBOURNE'
    collapse to the same key.
    """
    if not description:
        return ''
    s = description.upper()
    # Strip dates/numbers at the start (e.g., transfer reference codes)
    s = re.sub(r'^\d{6,}\s*', '', s)
    # Strip trailing trailing 4+-digit reference numbers
    s = re.sub(r'\s+\d{4,}\s*$', '', s)
    # Strip city names commonly appended by card networks (rough heuristic)
    s = re.sub(r',[A-Z\s]+$', '', s)
    # Collapse whitespace
    s = re.sub(r'\s+', ' ', s).strip()
    # Truncate to first 30 chars to avoid long descriptions counting as different
    return s[:30]


def _detect_cadence_days(dates_sorted):
    """Given a sorted list of dates, infer typical gap between them.

    Returns (median_gap_days, hits_count). If gaps are wildly inconsistent,
    median_gap_days will reflect that — caller decides if it's "subscription-like".
    """
    if len(dates_sorted) < 2:
        return None, len(dates_sorted)
    gaps = []
    for i in range(1, len(dates_sorted)):
        gaps.append((dates_sorted[i] - dates_sorted[i - 1]).days)
    gaps.sort()
    mid = len(gaps) // 2
    median_gap = gaps[mid] if len(gaps) % 2 == 1 else (gaps[mid - 1] + gaps[mid]) // 2
    return median_gap, len(dates_sorted)


def _classify_cadence(median_gap_days):
    """Map a median gap to a friendly cadence label, or None if not periodic."""
    if median_gap_days is None:
        return None
    if 5 <= median_gap_days <= 9:
        return 'weekly'
    if 12 <= median_gap_days <= 17:
        return 'fortnightly'
    if 26 <= median_gap_days <= 35:
        return 'monthly'
    return None


def subscription_audit_suggestions(today=None):
    if today is None:
        today = date.today()

    cutoff = (today - timedelta(days=LOOKBACK_DAYS)).isoformat()

    with get_db() as conn:
        rows = conn.execute(
            "SELECT date, amount, description "
            "FROM transactions "
            "WHERE date >= ? "
            "AND amount < 0 "
            "AND amount > ? "
            "AND (is_internal_transfer = 0 OR is_internal_transfer IS NULL) "
            "ORDER BY date ASC",
            (cutoff, -SUBSCRIPTION_AMOUNT_MAX)
        ).fetchall()

    # Group by normalised payee
    by_payee = defaultdict(list)
    for r in rows:
        key = _normalise_payee(r['description'])
        if not key:
            continue
        # Parse date
        try:
            from datetime import datetime
            d = datetime.strptime(r['date'], '%Y-%m-%d').date()
        except (ValueError, TypeError):
            continue
        by_payee[key].append({'date': d, 'amount': abs(r['amount']), 'desc': r['description']})

    # Identify subscriptions
    subscriptions = []
    for payee_key, hits in by_payee.items():
        # Require at least 3 hits — 2 with consistent timing could be coincidence
        if len(hits) < 3:
            continue
        # Amount should be reasonably consistent — coefficient of variation < 25%
        amounts = [h['amount'] for h in hits]
        avg_amt = sum(amounts) / len(amounts)
        if avg_amt == 0:
            continue
        variance = sum((a - avg_amt) ** 2 for a in amounts) / len(amounts)
        std_dev = variance ** 0.5
        if std_dev / avg_amt > 0.25:
            continue  # Too variable — not a subscription, more like takeaway

        # Cadence check
        sorted_dates = sorted(h['date'] for h in hits)
        median_gap, count = _detect_cadence_days(sorted_dates)
        cadence = _classify_cadence(median_gap)
        if cadence is None:
            continue

        # Project monthly cost
        if cadence == 'weekly':
            monthly_estimate = avg_amt * 4.33
        elif cadence == 'fortnightly':
            monthly_estimate = avg_amt * 2.17
        else:  # monthly
            monthly_estimate = avg_amt

        # Last seen — for low-utilisation flag
        last_hit = max(h['date'] for h in hits)
        days_since_last = (today - last_hit).days

        subscriptions.append({
            'payee': hits[0]['desc'][:40],  # use original cleaner desc for display
            'avg_amount': round(avg_amt, 2),
            'cadence': cadence,
            'count': count,
            'monthly_estimate': round(monthly_estimate, 2),
            'days_since_last': days_since_last,
        })

    if not subscriptions:
        return []

    # Sort by monthly estimate descending — biggest leak first
    subscriptions.sort(key=lambda s: s['monthly_estimate'], reverse=True)
    total_monthly = sum(s['monthly_estimate'] for s in subscriptions)

    # Low utilisation = haven't seen for 60+ days
    stale = [s for s in subscriptions if s['days_since_last'] >= 60]

    out = []

    # Headline suggestion: total subscription bleed
    if len(subscriptions) >= 3:
        # List top 3 inline
        top = subscriptions[:3]
        names = ', '.join(s['payee'].split(',')[0].strip().title() for s in top)
        more = len(subscriptions) - 3
        more_text = f' (and {more} more)' if more > 0 else ''
        out.append({
            'icon': '🔁',
            'priority': 'good',
            'text': (f'<strong>{len(subscriptions)} active subscriptions</strong> '
                     f'totalling ~<strong>${total_monthly:.0f}/month</strong>.'),
            'reasoning': (f'Top spend: {names}{more_text}. '
                          f'Worth a quarterly audit — at ${total_monthly * 12:.0f}/year, '
                          f'cancelling 1-2 unused ones is meaningful money.'),
            'action': 'Review subs',
            'action_url': '/transactions?cat=Subscriptions',
            'kind': 'subscription_audit',
        })

    # Stale subscription warning — separate item, only if there are clearly idle ones
    if stale:
        stale_total = sum(s['monthly_estimate'] for s in stale)
        names = ', '.join(s['payee'].split(',')[0].strip().title() for s in stale[:3])
        more_text = f' and {len(stale) - 3} more' if len(stale) > 3 else ''
        out.append({
            'icon': '🪦',
            'priority': 'attention',
            'text': (f'<strong>{len(stale)} subscription{"s" if len(stale) != 1 else ""}</strong> '
                     f'haven\'t been used recently (~<strong>${stale_total:.0f}/month</strong>).'),
            'reasoning': (f'{names}{more_text} — last charge was 60+ days ago but '
                          f'still recurring. Either cancel or move to annual billing for a discount.'),
            'action': 'Cancel idle',
            'action_url': '/transactions?cat=Subscriptions',
            'kind': 'subscription_stale',
        })

    return out
