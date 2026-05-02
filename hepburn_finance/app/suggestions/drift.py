"""Discretionary spending drift detection.

Focuses on categories where a 'cap suggestion' makes sense: takeaway,
delivery, entertainment, shopping. When recent spend in these categories
trends up significantly, suggests a concrete weekly or monthly cap based
on the prior baseline.

The actionable framing matters: "Takeaway is up 47%, cap at $300/month?"
gives the user a specific decision to make rather than a vague observation.
"""
from datetime import date, timedelta

from app.database import get_db


# Categories where a cap is a reasonable response to upward drift
DISCRETIONARY_CATEGORIES = {
    'Food · Takeaway',
    'Food · Delivery',
    'Food · Restaurant',
    'Food · Cafe',
    'Entertainment',
    'Entertainment · Movies',
    'Entertainment · Streaming',
    'Shopping',
    'Shopping · Amazon',
    'Shopping · Online',
    'Shopping · Clothing',
    'Personal · Beauty',
    'Personal · Hobbies',
    'Alcohol',
    'Bars & Pubs',
}


def discretionary_drift_suggestions(today=None):
    if today is None:
        today = date.today()

    recent_start = today - timedelta(days=30)
    baseline_start = today - timedelta(days=120)
    baseline_end = today - timedelta(days=30)

    with get_db() as conn:
        recent_rows = conn.execute(
            "SELECT category, SUM(ABS(amount)) AS total, COUNT(*) AS hits "
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

    recent = {r['category']: r for r in recent_rows}
    baseline = {r['category']: r['total'] for r in baseline_rows}

    movements = []
    for cat in DISCRETIONARY_CATEGORIES:
        if cat not in recent:
            continue
        rec = recent[cat]
        rec_total = rec['total']
        rec_hits = rec['hits']
        # Baseline is 90 days, normalise to 30
        baseline_total = baseline.get(cat, 0)
        baseline_30d = baseline_total / 3.0
        if baseline_30d < 50:
            continue
        delta_dollars = rec_total - baseline_30d
        if delta_dollars < 50:
            continue
        delta_pct = (delta_dollars / baseline_30d) * 100
        if delta_pct < 30:
            continue
        movements.append({
            'category': cat,
            'recent': rec_total,
            'recent_hits': rec_hits,
            'baseline_30d': baseline_30d,
            'delta_dollars': delta_dollars,
            'delta_pct': delta_pct,
        })

    if not movements:
        return []

    movements.sort(key=lambda m: m['delta_dollars'], reverse=True)
    biggest = movements[0]

    # Suggest a cap that's the average of recent and baseline (a soft pullback)
    suggested_cap = round((biggest['recent'] + biggest['baseline_30d']) / 2 / 50) * 50
    if suggested_cap < 100:
        suggested_cap = 100  # Floor

    # What weekly equivalent?
    weekly = round(suggested_cap / 4.33)

    # Build action URL that pre-fills the budget form. We pass weekly amount
    # because budgets typically work better on a calendar-week cadence.
    from urllib.parse import urlencode
    action_qs = urlencode({
        'name': f'{biggest["category"].split("·")[-1].strip() or biggest["category"]} cap',
        'category': biggest['category'],
        'amount': weekly,
        'cadence': 'weekly',
    })

    out = [{
        'icon': '🎯',
        'priority': 'attention',
        'text': (f'<strong>{biggest["category"]}</strong> spend up '
                 f'<strong>{biggest["delta_pct"]:+.0f}%</strong> '
                 f'(${biggest["baseline_30d"]:.0f} → ${biggest["recent"]:.0f}, '
                 f'{biggest["recent_hits"]} transactions).'),
        'reasoning': (f'Quick win: try a ${suggested_cap}/month cap '
                      f'(~${weekly}/week). Halfway between recent and your usual rate. '
                      f'Saving ${biggest["recent"] - suggested_cap:.0f} this month if you stick to it.'),
        'action': 'Set cap',
        'action_url': f'/budgets/new?{action_qs}',
        'kind': 'discretionary_drift',
    }]

    return out
