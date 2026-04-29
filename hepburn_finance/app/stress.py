"""Cash flow stress assessment and smart transfer suggestions.

The stress meter takes the 30-day forecast and decides:
- tier: green (safe), amber (tight), red (likely shortfall)
- days_until_zero
- bills_coverage %
- lowest_balance and date

The suggestions engine produces actionable advice:
- Smart transfer recommendations to avoid forecast lows
- Plan expiry alerts
- Subscription leak detection
- Salary timing reminders
"""
from datetime import date, timedelta
from app.database import get_db
from app.forecast import forecast_daily_balances, get_starting_balance, parse_iso


# Thresholds — configurable via settings table later
AMBER_FLOOR = 500
RED_FLOOR = 0
COVERAGE_AMBER = 80


def compute_stress(account_ids, today=None):
    """Return a dict with tier, dtz, coverage, lowest_balance, lowest_date."""
    if today is None:
        today = date.today()
    balances, starting, instances = forecast_daily_balances(account_ids, days_ahead=30, today=today)

    if not balances:
        return {
            'tier': 'green', 'message': 'No forecast data',
            'dtz': None, 'coverage': 100, 'lowest': starting, 'lowest_date': None,
            'starting_balance': starting, 'total_in': 0, 'total_out': 0,
        }

    # Lowest balance and its date
    lowest = starting
    lowest_date = today.isoformat()
    for d_iso, bal in balances.items():
        if bal < lowest:
            lowest = bal
            lowest_date = d_iso

    # Days until zero
    dtz = None
    for d_iso in sorted(balances.keys()):
        if balances[d_iso] <= 0:
            d_obj = date.fromisoformat(d_iso)
            dtz = (d_obj - today).days
            break

    # Coverage: cash + 30d income / 30d outflow
    total_in = sum(i['amount'] for i in instances if i['amount'] > 0)
    total_out = -sum(i['amount'] for i in instances if i['amount'] < 0)
    if total_out > 0:
        coverage = round((starting + total_in) / total_out * 100)
    else:
        coverage = 200  # nothing going out — effectively unlimited

    # Tier
    if lowest < RED_FLOOR:
        tier = 'red'
        msg = f'Forecast goes negative — ${lowest:,.0f} on {lowest_date}. Action needed.'
    elif lowest < AMBER_FLOOR or coverage < COVERAGE_AMBER:
        tier = 'amber'
        msg = f'Forecast dips to ${lowest:,.0f} on {lowest_date}. Plan a top-up before then.'
    else:
        tier = 'green'
        msg = 'Forecast holds positive across the next 30 days.'

    return {
        'tier': tier,
        'message': msg,
        'dtz': dtz,
        'coverage': min(200, coverage),
        'lowest': round(lowest, 2),
        'lowest_date': lowest_date,
        'starting_balance': round(starting, 2),
        'total_in': round(total_in, 2),
        'total_out': round(total_out, 2),
    }


def smart_transfer_suggestions(selected_account_ids, today=None):
    """Generate transfer suggestions to address forecast lows.

    Returns list of suggestion dicts."""
    if today is None:
        today = date.today()

    suggestions = []
    stress = compute_stress(selected_account_ids, today)

    # 1. Forecast low — suggest transfer if there's a surplus account
    if stress['tier'] in ('amber', 'red') and stress['lowest_date']:
        with get_db() as conn:
            others = conn.execute(
                'SELECT * FROM accounts WHERE archived=0 AND id NOT IN ({}) '
                'AND type IN (\'transaction\',\'savings\') AND balance > 0 '
                'ORDER BY balance DESC'.format(
                    ','.join('?' * len(selected_account_ids)) or 'NULL'
                ),
                tuple(selected_account_ids)
            ).fetchall()

        need = max(AMBER_FLOOR - stress['lowest'], 100)
        # Round to nearest $10 for human-friendly amount
        need_rounded = ((int(need) // 10) + 1) * 10

        if others and others[0]['balance'] >= need_rounded:
            source = others[0]
            suggestions.append({
                'icon': '→',
                'priority': 'urgent' if stress['tier'] == 'red' else 'attention',
                'text': (f'Move <strong>${need_rounded}</strong> from <strong>{source["name"]}</strong> '
                         f'into your forecast accounts before {stress["lowest_date"]}.'),
                'reasoning': (f'Forecast hits ${stress["lowest"]:,.2f} that day — this restores '
                              f'the buffer above ${AMBER_FLOOR}.'),
                'action': 'Plan transfer',
            })
        else:
            suggestions.append({
                'icon': '⚠',
                'priority': 'urgent',
                'text': f'Cash flow gap of <strong>${stress["lowest"]:,.2f}</strong> on {stress["lowest_date"]}.',
                'reasoning': ('No surplus account has enough to cover the gap. '
                              'Options: defer non-critical bills, request a payment plan, '
                              'or use offset funds if available.'),
                'action': 'Review options',
            })

    # 2. Interest-free plan expiring soon
    with get_db() as conn:
        plans = conn.execute(
            'SELECT name, current_balance, expiry_date '
            'FROM interest_free_plans '
            'WHERE expiry_date >= ? AND current_balance > 0 '
            'ORDER BY expiry_date ASC',
            (today.isoformat(),)
        ).fetchall()

    for p in plans[:2]:
        try:
            expiry = parse_iso(p['expiry_date'])
            days_left = (expiry - today).days
        except (ValueError, TypeError):
            continue
        if days_left < 30:
            suggestions.append({
                'icon': '⏰',
                'priority': 'attention',
                'text': (f'<strong>{p["name"]}</strong> interest-free plan expires in '
                         f'{days_left} days with ${p["current_balance"]:.2f} outstanding.'),
                'reasoning': ('If unpaid by then, the balance rolls onto the Expired Plan Rate '
                              '(typically 29.99%). Worth scheduling a payment now.'),
                'action': 'Schedule payment',
            })

    # 3. Subscription leak
    with get_db() as conn:
        sub_count = conn.execute(
            "SELECT COUNT(DISTINCT description) AS c, SUM(amount) AS s "
            "FROM transactions WHERE category='Subscriptions' "
            "AND date >= date('now', '-30 days')"
        ).fetchone()

    if sub_count and sub_count['c'] >= 3:
        total = abs(sub_count['s'] or 0)
        suggestions.append({
            'icon': '✂',
            'priority': 'good',
            'text': (f'<strong>{sub_count["c"]} different subscriptions</strong> '
                     f'totalling ${total:.2f} this month.'),
            'reasoning': ('Streaming and software subscriptions overlap often. '
                          'Worth auditing which ones the family actually used in the last 30 days.'),
            'action': 'Review subs',
        })

    return suggestions


def debt_attack_order():
    """Build the situation-aware debt repayment order.

    Priorities:
    1. Anything with imminent rate change (interest-free plans expiring soon)
    2. Active high-rate balances (28%+ purchase rate, after plans)
    3. Other expiring plans
    4. PPOR mortgage
    5. Investment loan principal (deductible — lowest priority for extra repayments)
    """
    today = date.today()
    items = []

    # Plans expiring < 30d
    with get_db() as conn:
        urgent_plans = conn.execute(
            "SELECT * FROM interest_free_plans "
            "WHERE current_balance > 0 AND expiry_date <= date('now','+30 days') "
            "ORDER BY expiry_date ASC"
        ).fetchall()

        soon_plans = conn.execute(
            "SELECT * FROM interest_free_plans "
            "WHERE current_balance > 0 "
            "AND expiry_date > date('now','+30 days') "
            "AND expiry_date <= date('now','+90 days') "
            "ORDER BY expiry_date ASC"
        ).fetchall()

        accounts = conn.execute(
            "SELECT * FROM accounts WHERE archived=0 AND balance < 0 "
            "ORDER BY interest_rate DESC NULLS LAST"
        ).fetchall()

    rank = 1
    for p in urgent_plans:
        try:
            days = (parse_iso(p['expiry_date']) - today).days
        except (ValueError, TypeError):
            days = 99
        items.append({
            'rank': rank,
            'priority': 1,
            'name': p['name'],
            'detail': f'Expires <strong>in {days} days</strong> · ${p["current_balance"]:.2f} outstanding',
            'amount': -p['current_balance'],
            'rate': '0% → 30%',
            'rate_class': 'hot',
        })
        rank += 1

    # Active high-rate credit balances (any credit account with debt outside its plans)
    for acc in accounts:
        if acc['type'] != 'credit':
            continue
        # Sum of plans for this account
        with get_db() as conn:
            plan_total = conn.execute(
                'SELECT COALESCE(SUM(current_balance), 0) AS t FROM interest_free_plans WHERE account_id=?',
                (acc['id'],)
            ).fetchone()
        outside = abs(acc['balance']) - (plan_total['t'] or 0)
        if outside > 100:
            rate = acc['interest_rate'] or 28.49
            items.append({
                'rank': rank,
                'priority': 1,
                'name': f'{acc["name"]} balance outside plans',
                'detail': f'Active interest at <strong>{rate:.2f}%</strong> on ~${outside:,.0f}',
                'amount': -outside,
                'rate': f'{rate:.2f}%',
                'rate_class': 'hot',
            })
            rank += 1

    # Other expiring plans (30-90 days)
    if soon_plans:
        total = sum(p['current_balance'] for p in soon_plans)
        items.append({
            'rank': rank,
            'priority': 2,
            'name': f'Other plans expiring (next 90d)',
            'detail': ' · '.join(f'{p["name"]} (${p["current_balance"]:.0f})' for p in soon_plans[:3]),
            'amount': -total,
            'rate': '0% → 30%',
            'rate_class': 'warm',
        })
        rank += 1

    # PPOR
    for acc in accounts:
        if acc['type'] == 'ppor':
            rate = acc['interest_rate']
            if rate is None:
                rate_str = '~6.0% *'
            else:
                rate_str = f'{rate:.1f}%'
            items.append({
                'rank': rank,
                'priority': 3,
                'name': acc['name'],
                'detail': 'Owner-occupier — non-deductible interest',
                'amount': acc['balance'],
                'rate': rate_str,
                'rate_class': 'cool',
            })
            rank += 1

    # Personal loans (solar, car, etc.) — non-deductible, formal
    for acc in accounts:
        if acc['type'] == 'loan_personal':
            rate = acc['interest_rate']
            if rate is None:
                rate_str = '—'
            else:
                rate_str = f'{rate:.1f}%'
            items.append({
                'rank': rank,
                'priority': 3,
                'name': acc['name'],
                'detail': 'Personal loan — non-deductible interest',
                'amount': acc['balance'],
                'rate': rate_str,
                'rate_class': 'cool',
            })
            rank += 1

    # Investment loans
    for acc in accounts:
        if acc['type'] == 'loan_investment':
            rate = acc['interest_rate']
            if rate is None:
                rate_str = '~6.0% *'
            else:
                rate_str = f'{rate:.1f}%'
            items.append({
                'rank': rank,
                'priority': 3,
                'name': acc['name'],
                'detail': 'Investment property — <strong>tax deductible interest</strong>',
                'amount': acc['balance'],
                'rate': rate_str,
                'rate_class': 'deductible',
            })
            rank += 1

    # Informal loans (borrowed from family/friends/work) — usually 0% but socially-prioritised
    for acc in accounts:
        if acc['type'] == 'loan_informal':
            rate = acc['interest_rate']
            if rate is None or rate == 0:
                rate_str = '0% — informal'
            else:
                rate_str = f'{rate:.1f}%'
            items.append({
                'rank': rank,
                'priority': 4,
                'name': acc['name'],
                'detail': 'Informal — owed to family / friend / work',
                'amount': acc['balance'],
                'rate': rate_str,
                'rate_class': 'informal',
            })
            rank += 1

    return items
