"""Cash flow suggestions: forecast gaps, transfer suggestions, bill clustering."""
from datetime import date, timedelta
from collections import defaultdict

from app.database import get_db
from app.forecast import forecast_daily_balances, expand_bills, parse_iso


GREEN_FLOOR = 1500
AMBER_FLOOR = 200


def cashflow_suggestions(account_ids, today=None):
    """Forecast gap detection — surfaces shortfalls and proposes transfers."""
    if today is None:
        today = date.today()

    if not account_ids:
        return []

    balances, _starting, _instances = forecast_daily_balances(
        account_ids, days_ahead=30, today=today
    )
    if not balances:
        return []

    lowest = min(balances.values())
    lowest_date = next((d for d, v in balances.items() if v == lowest), None)

    out = []

    # No gap to address
    if lowest >= AMBER_FLOOR:
        return out

    # Look for surplus accounts not in the forecast set
    placeholders = ','.join('?' * len(account_ids)) if account_ids else 'NULL'
    with get_db() as conn:
        others = conn.execute(
            f"SELECT id, name, balance, opening_balance FROM accounts "
            f"WHERE archived=0 AND id NOT IN ({placeholders}) "
            f"AND type IN ('transaction','savings') "
            f"AND COALESCE(opening_balance, balance) > 0 "
            f"ORDER BY COALESCE(opening_balance, balance) DESC",
            tuple(account_ids)
        ).fetchall()

    need = max(AMBER_FLOOR - lowest, 100)
    need_rounded = ((int(need) // 10) + 1) * 10
    tier = 'red' if lowest < 0 else 'amber'

    if others and (others[0]['opening_balance'] or others[0]['balance']) >= need_rounded:
        source = others[0]
        out.append({
            'icon': '→',
            'priority': 'urgent' if tier == 'red' else 'attention',
            'text': (f'Move <strong>${need_rounded}</strong> from <strong>{source["name"]}</strong> '
                     f'into your forecast accounts before {lowest_date}.'),
            'reasoning': (f'Forecast hits ${lowest:,.2f} that day — this restores '
                          f'the buffer above ${AMBER_FLOOR}.'),
            'action': 'Plan transfer',
            'action_endpoint': 'main.new_transfer',
            'action_kwargs': {'from_account_id': source['id'], 'amount': need_rounded},
            'kind': 'cashflow_transfer_in',
        })
    else:
        out.append({
            'icon': '⚠',
            'priority': 'urgent',
            'text': f'Cash flow gap of <strong>${lowest:,.2f}</strong> on {lowest_date}.',
            'reasoning': ('No surplus account has enough to cover the gap. '
                          'Options: defer non-critical bills, request a payment plan, '
                          'or use offset funds if available.'),
            'action': 'Review options',
            'action_endpoint': 'main.list_transactions',
            'action_kwargs': {},
            'kind': 'cashflow_gap',
        })

    return out


def bill_clustering_suggestions(account_ids, today=None):
    """Detect days where many bills hit before income arrives.

    A 'cluster' is a single day with >= 3 bills totalling > $300, where the
    forecast on that day is below GREEN_FLOOR. Suggests concrete actions:
    request a date change with the biller, or sweep funds in advance.
    """
    if today is None:
        today = date.today()

    if not account_ids:
        return []

    horizon = today + timedelta(days=14)
    bills = expand_bills(today, horizon, account_ids)
    bills_only = [b for b in bills if not b.get('is_income') and b['amount'] < 0]

    # Group by date, pick days with >= 3 bills totalling > $300
    by_day = defaultdict(list)
    for b in bills_only:
        by_day[b['date']].append(b)

    balances, _starting, _instances = forecast_daily_balances(
        account_ids, days_ahead=14, today=today
    )

    out = []
    for d, day_bills in sorted(by_day.items()):
        if len(day_bills) < 3:
            continue
        total = sum(abs(b['amount']) for b in day_bills)
        if total < 300:
            continue
        bal_that_day = balances.get(d.isoformat(), 0)
        # Only flag if balance after this cluster is below floor
        if bal_that_day >= GREEN_FLOOR:
            continue

        # Identify the biggest 1-2 bills as the "shift candidate" — those have
        # the most leverage if their date can move.
        sorted_bills = sorted(day_bills, key=lambda b: abs(b['amount']), reverse=True)
        biggest = sorted_bills[0]

        days_until = (d - today).days
        when = (
            'today' if days_until == 0
            else 'tomorrow' if days_until == 1
            else f'in {days_until} days'
        )

        out.append({
            'icon': '📅',
            'priority': 'attention' if bal_that_day >= 0 else 'urgent',
            'text': (f'<strong>{len(day_bills)} bills</strong> hit on {d.strftime("%a %d %b")} ({when}) '
                     f'totalling <strong>${total:,.0f}</strong>.'),
            'reasoning': (f'After they clear, balance drops to ${bal_that_day:,.0f}. '
                          f'Quick wins: call {biggest["name"]} (largest at ${abs(biggest["amount"]):,.0f}) '
                          f'to shift its debit by 5-7 days, or pre-load the account with a transfer.'),
            'action': 'Plan it',
            'action_endpoint': 'main.new_transfer',
            'action_kwargs': {},
            'kind': 'bill_cluster',
        })

    # Cap at 2 — don't drown the user in cluster warnings if many days have them
    return out[:2]
