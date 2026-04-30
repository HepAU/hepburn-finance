"""Interest-free plan expiry warnings."""
from datetime import date

from app.database import get_db
from app.forecast import parse_iso


def interest_free_plan_suggestions(today=None):
    if today is None:
        today = date.today()

    out = []
    with get_db() as conn:
        plans = conn.execute(
            'SELECT name, current_balance, expiry_date '
            'FROM interest_free_plans '
            'WHERE expiry_date >= ? AND current_balance > 0 '
            'ORDER BY expiry_date ASC',
            (today.isoformat(),)
        ).fetchall()

    for p in plans[:3]:
        try:
            expiry = parse_iso(p['expiry_date'])
            days_left = (expiry - today).days
        except (ValueError, TypeError):
            continue
        if days_left < 30:
            priority = 'urgent' if days_left < 14 else 'attention'
            out.append({
                'icon': '⏰',
                'priority': priority,
                'text': (f'<strong>{p["name"]}</strong> interest-free plan expires in '
                         f'{days_left} days with ${p["current_balance"]:.2f} outstanding.'),
                'reasoning': ('If unpaid by then, the balance rolls onto the Expired Plan Rate '
                              '(typically 29.99%). Worth scheduling a payment now.'),
                'action': 'Schedule payment',
                'kind': 'plan_expiry',
            })

    return out
