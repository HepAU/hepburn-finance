"""Cash flow forecasting.

Takes scheduled bills and transfers, expands them into specific dated
instances respecting recurrence/end-date/occurrence-cap rules, and projects
running balance forward from today.

For accounts where it is set, `available` (the spendable amount today,
including pending holds) is used as the starting balance — this matches
what banks show as the actionable figure. Falls back to `balance` when
`available` is null.

Transfers move money between two accounts. They are netted out of the
running balance based on which accounts the user has selected for the
forecast:
  - source selected, destination not  -> applies as -amount
  - destination selected, source not  -> applies as +amount
  - both selected                     -> no net effect
  - neither selected                  -> ignored
"""
from datetime import datetime, timedelta, date
from app.database import get_db


def parse_iso(s):
    return datetime.strptime(s, '%Y-%m-%d').date()


def add_period(d, recurring):
    """Advance a date by one period of the given recurrence type."""
    if recurring == 'weekly':
        return d + timedelta(days=7)
    if recurring == 'fortnightly':
        return d + timedelta(days=14)
    if recurring == 'monthly':
        month = d.month + 1
        year = d.year + (1 if month > 12 else 0)
        month = ((month - 1) % 12) + 1
        try:
            return d.replace(year=year, month=month)
        except ValueError:
            for day in range(d.day, 0, -1):
                try:
                    return d.replace(year=year, month=month, day=day)
                except ValueError:
                    continue
    if recurring == 'quarterly':
        month = d.month + 3
        year = d.year + (month - 1) // 12
        month = ((month - 1) % 12) + 1
        try:
            return d.replace(year=year, month=month)
        except ValueError:
            for day in range(d.day, 0, -1):
                try:
                    return d.replace(year=year, month=month, day=day)
                except ValueError:
                    continue
    if recurring == 'yearly':
        try:
            return d.replace(year=d.year + 1)
        except ValueError:
            return d.replace(year=d.year + 1, day=28)
    return None


def _expand_recurrence(start_date, recurring, from_d, to_d, end_date=None, max_occurrences=None):
    """Return list of dates in [from_d, to_d] for a series.

    Respects optional `end_date` and `max_occurrences` caps.
    """
    out = []
    cursor = start_date
    occurrence = 0

    if recurring == 'once':
        if from_d <= cursor <= to_d:
            out.append(cursor)
        return out

    safety = 0
    while cursor < from_d and safety < 365:
        nxt = add_period(cursor, recurring)
        if not nxt:
            break
        cursor = nxt
        occurrence += 1
        safety += 1

    safety = 0
    while cursor <= to_d and safety < 200:
        if end_date and cursor > end_date:
            break
        if max_occurrences is not None and occurrence >= max_occurrences:
            break
        if cursor >= from_d:
            out.append(cursor)
        nxt = add_period(cursor, recurring)
        if not nxt:
            break
        cursor = nxt
        occurrence += 1
        safety += 1

    return out


def expand_bills(from_date, to_date, account_ids=None):
    """Return list of bill instances in window."""
    instances = []
    with get_db() as conn:
        if account_ids:
            placeholders = ','.join('?' * len(account_ids))
            bills = conn.execute(
                f'SELECT * FROM scheduled_bills WHERE active=1 AND account_id IN ({placeholders})',
                tuple(account_ids)
            ).fetchall()
        else:
            bills = conn.execute('SELECT * FROM scheduled_bills WHERE active=1').fetchall()

    for b in bills:
        try:
            start = parse_iso(b['next_date'])
        except (ValueError, TypeError):
            continue
        end = None
        if b['end_date']:
            try:
                end = parse_iso(b['end_date'])
            except (ValueError, TypeError):
                end = None
        max_occ = b['occurrences_remaining']
        dates = _expand_recurrence(start, b['recurring'], from_date, to_date, end, max_occ)
        for d in dates:
            instances.append({
                'date': d,
                'id': b['id'],
                'name': b['name'],
                'amount': b['amount'],
                'category': b['category'] or 'Uncategorised',
                'account_id': b['account_id'],
                'is_income': bool(b['is_income']),
                'recurring': b['recurring'],
                'kind': 'bill',
            })

    return sorted(instances, key=lambda i: (i['date'], i['name']))


def expand_transfers(from_date, to_date, selected_account_ids):
    """Return list of transfer instances with their net effect on the forecast.

    `net_effect` is the amount applied to the running balance:
      - source selected only: -amount
      - dest selected only: +amount
      - both selected: 0 (internal — money moves but doesn't leave)
      - neither selected: None (transfer is irrelevant to this forecast)
    """
    instances = []
    selected = set(selected_account_ids or [])

    with get_db() as conn:
        transfers = conn.execute(
            'SELECT * FROM scheduled_transfers WHERE active=1'
        ).fetchall()

    for t in transfers:
        try:
            start = parse_iso(t['next_date'])
        except (ValueError, TypeError):
            continue
        end = None
        if t['end_date']:
            try:
                end = parse_iso(t['end_date'])
            except (ValueError, TypeError):
                end = None
        max_occ = t['occurrences_remaining']
        dates = _expand_recurrence(start, t['recurring'], from_date, to_date, end, max_occ)

        from_in = t['from_account_id'] in selected
        to_in = t['to_account_id'] in selected
        if from_in and to_in:
            net = 0
        elif from_in:
            net = -abs(t['amount'])
        elif to_in:
            net = abs(t['amount'])
        else:
            net = None

        for d in dates:
            instances.append({
                'date': d,
                'id': t['id'],
                'name': t['name'],
                'amount': t['amount'],
                'from_account_id': t['from_account_id'],
                'to_account_id': t['to_account_id'],
                'net_effect': net,
                'category': t['category'] or 'Transfer',
                'recurring': t['recurring'],
                'kind': 'transfer',
            })

    return sorted(instances, key=lambda i: (i['date'], i['name']))


def get_starting_balance(account_ids):
    """Spendable cash today across the selected accounts.

    Uses `available` when set (bank's "spendable now" figure that nets out
    pending holds), otherwise the computed balance from opening_balance +
    transactions. This is what the user could actually spend right now.
    """
    if not account_ids:
        return 0.0
    from app.balances import compute_account_balance
    with get_db() as conn:
        placeholders = ','.join('?' * len(account_ids))
        rows = conn.execute(
            f'SELECT id, balance, opening_balance, available, type '
            f'FROM accounts WHERE id IN ({placeholders})',
            tuple(account_ids)
        ).fetchall()
        total = 0.0
        for r in rows:
            if r['available'] is not None:
                total += r['available']
            else:
                total += compute_account_balance(r, conn=conn)
    return total


def forecast_daily_balances(account_ids, days_ahead=30, today=None):
    """Walk day-by-day from today through today+days_ahead.

    Returns (balances_dict, starting_balance, all_instances)
      balances_dict: {iso_date: running_balance}
      starting_balance: spendable cash today (uses available, falls back to balance)
      all_instances: every bill + transfer instance with kind set so the
                     calendar can render them differently.
    """
    if today is None:
        today = date.today()
    end = today + timedelta(days=days_ahead)
    starting = get_starting_balance(account_ids)

    bill_instances = expand_bills(today, end, account_ids)
    transfer_instances = expand_transfers(today, end, account_ids)

    effects = {}
    for b in bill_instances:
        effects.setdefault(b['date'], []).append(b['amount'])
    for t in transfer_instances:
        if t['net_effect'] not in (None, 0):
            effects.setdefault(t['date'], []).append(t['net_effect'])

    balances = {}
    running = starting
    cursor = today
    while cursor <= end:
        for amt in effects.get(cursor, []):
            running += amt
        balances[cursor.isoformat()] = round(running, 2)
        cursor += timedelta(days=1)

    all_instances = bill_instances + [t for t in transfer_instances if t['net_effect'] is not None]
    all_instances.sort(key=lambda i: (i['date'], i.get('name', '')))

    return balances, starting, all_instances
