"""Category trend analysis.

Compares the last 30 days' spend per category to the prior 60-day average
(months 2-3 back). Surfaces categories where spend has moved >25% in either
direction. Helps spot leaks (categories trending up) and successes
(categories where you've cut back).

Uses absolute thresholds in addition to percentage to avoid noise: a category
that went from $5 to $10 is +100% but irrelevant. We only flag movements
above $40 in absolute dollars.
"""
import re
from datetime import date, timedelta
from collections import defaultdict

from app.database import get_db


# Categories that aren't actionable — skip them in trend analysis
SKIP_CATEGORIES = {
    'Income', 'Transfer · Internal',
    'Bank fees · Dishonour fee',  # Outside user's control most of the time
    None, '', 'Uncategorised',
}

# Family categories that follow income, not behaviour — skip
SKIP_PREFIXES = ('Income ·', 'Mortgage ·', 'Transfer ·')

# Categories that the discretionary drift module handles with a more specific
# "set a cap" framing — let that module surface those rather than this one,
# so we don't double-report the same finding.
DISCRETIONARY_HANDLED_BY_DRIFT = {
    'Food · Takeaway', 'Food · Delivery', 'Food · Restaurant', 'Food · Cafe',
    'Entertainment', 'Entertainment · Movies', 'Entertainment · Streaming',
    'Shopping', 'Shopping · Amazon', 'Shopping · Online', 'Shopping · Clothing',
    'Personal · Beauty', 'Personal · Hobbies', 'Alcohol', 'Bars & Pubs',
}

MOVEMENT_PCT_THRESHOLD = 25
MOVEMENT_DOLLAR_THRESHOLD = 40
MIN_BASELINE = 30  # Don't compare against tiny categories


def category_trend_suggestions(today=None):
    if today is None:
        today = date.today()

    recent_start = today - timedelta(days=30)
    baseline_start = today - timedelta(days=90)
    baseline_end = today - timedelta(days=30)

    with get_db() as conn:
        recent_rows = conn.execute(
            "SELECT category, SUM(ABS(amount)) AS total "
            "FROM transactions "
            "WHERE amount < 0 "
            "AND date >= ? AND date <= ? "
            "AND (is_internal_transfer = 0 OR is_internal_transfer IS NULL) "
            "GROUP BY category",
            (recent_start.isoformat(), today.isoformat())
        ).fetchall()
        baseline_rows = conn.execute(
            "SELECT category, SUM(ABS(amount)) AS total "
            "FROM transactions "
            "WHERE amount < 0 "
            "AND date >= ? AND date < ? "
            "AND (is_internal_transfer = 0 OR is_internal_transfer IS NULL) "
            "GROUP BY category",
            (baseline_start.isoformat(), baseline_end.isoformat())
        ).fetchall()

    recent = {r['category']: r['total'] for r in recent_rows}
    baseline = {r['category']: r['total'] for r in baseline_rows}

    # Bail if we don't have enough history
    total_baseline_days = sum(b for b in baseline.values())
    if total_baseline_days < 100:
        return []  # Need at least ~$100 of historical spend to be meaningful

    # Compute deltas
    movements = []
    for cat, recent_total in recent.items():
        if cat in SKIP_CATEGORIES:
            continue
        if cat in DISCRETIONARY_HANDLED_BY_DRIFT:
            continue
        if cat and any(cat.startswith(p) for p in SKIP_PREFIXES):
            continue
        baseline_total = baseline.get(cat, 0)
        # Normalise baseline to a 30-day equivalent (it's 60 days)
        baseline_30d = baseline_total / 2.0
        if baseline_30d < MIN_BASELINE:
            continue
        delta_dollars = recent_total - baseline_30d
        if abs(delta_dollars) < MOVEMENT_DOLLAR_THRESHOLD:
            continue
        delta_pct = (delta_dollars / baseline_30d) * 100
        if abs(delta_pct) < MOVEMENT_PCT_THRESHOLD:
            continue
        movements.append({
            'category': cat,
            'recent': recent_total,
            'baseline_30d': baseline_30d,
            'delta_dollars': delta_dollars,
            'delta_pct': delta_pct,
        })

    if not movements:
        return []

    # Take the biggest mover (most actionable single insight)
    movements.sort(key=lambda m: abs(m['delta_dollars']), reverse=True)
    biggest = movements[0]

    out = []
    direction = 'up' if biggest['delta_dollars'] > 0 else 'down'
    if direction == 'up':
        out.append({
            'icon': '📈',
            'priority': 'attention',
            'text': (f'<strong>{biggest["category"]}</strong> spend up '
                     f'<strong>{biggest["delta_pct"]:+.0f}%</strong> vs prior 60-day average '
                     f'(${biggest["baseline_30d"]:.0f} → ${biggest["recent"]:.0f}).'),
            'reasoning': (f'That\'s ${biggest["delta_dollars"]:+.0f} more this month than your usual. '
                          f'Worth a quick look at what drove it.'),
            'action': 'See transactions',
            'action_endpoint': 'main.list_transactions',
            'action_kwargs': {'cat': biggest['category']},
            'kind': 'trend_up',
        })
    else:
        out.append({
            'icon': '📉',
            'priority': 'good',
            'text': (f'<strong>{biggest["category"]}</strong> spend down '
                     f'<strong>{biggest["delta_pct"]:+.0f}%</strong> vs your usual '
                     f'(saved ${abs(biggest["delta_dollars"]):.0f}).'),
            'reasoning': (f'Recent: ${biggest["recent"]:.0f}. Prior 30-day avg: ${biggest["baseline_30d"]:.0f}. '
                          f'Whatever you\'re doing, keep doing it.'),
            'action': 'See transactions',
            'action_endpoint': 'main.list_transactions',
            'action_kwargs': {'cat': biggest['category']},
            'kind': 'trend_down',
        })

    return out
