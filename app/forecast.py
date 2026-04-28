"""Cash flow forecasting.

Takes scheduled bills (with their recurrence rules), expands them into
specific dated instances, and projects running balance forward from today.
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
        # naive month advance — handles month-end correctly enough for our use
        month = d.month + 1
        year = d.year + (1 if month > 12 else 0)
        month = ((month - 1) % 12) + 1
        # Clamp day to last of new month
        try:
            return d.replace(year=year, month=month)
        except ValueError:
            # Day doesn't exist in new month (e.g. 31 Jan -> Feb)
            # Roll back to last valid day of new month
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
    return None  # 'once' — no next instance


def expand_bills(from_date, to_date, account_ids=None):
    """Return list of (date, bill_dict) instances within range, optionally
    filtered to specific account_ids."""
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
            cursor = parse_iso(b['next_date'])
        except (ValueError, TypeError):
            continue
        recurring = b['recurring']
        # Roll cursor forward to be >= from_date if it's a recurring bill that's drifted into the past
        safety = 0
        while cursor < from_date and recurring != 'once' and safety < 365:
            nxt = add_period(cursor, recurring)
            if not nxt:
                break
            cursor = nxt
            safety += 1

        # Now emit instances within window
        safety = 0
        while cursor <= to_date and safety < 200:
            if cursor >= from_date:
                instances.append({
                    'date': cursor,
                    'id': b['id'],
                    'name': b['name'],
                    'amount': b['amount'],
                    'category': b['category'] or 'Uncategorised',
                    'account_id': b['account_id'],
                    'is_income': bool(b['is_income']),
                    'recurring': recurring,
                })
            if recurring == 'once':
                break
            nxt = add_period(cursor, recurring)
            if not nxt:
                break
            cursor = nxt
            safety += 1

    return sorted(instances, key=lambda i: i['date'])


def get_starting_balance(account_ids):
    """Sum of current balances for the given account ids."""
    if not account_ids:
        return 0.0
    with get_db() as conn:
        placeholders = ','.join('?' * len(account_ids))
        row = conn.execute(
            f'SELECT COALESCE(SUM(balance), 0) AS total FROM accounts WHERE id IN ({placeholders})',
            tuple(account_ids)
        ).fetchone()
        return row['total'] or 0.0


def forecast_daily_balances(account_ids, days_ahead=30, today=None):
    """Return a dict {date_iso: running_balance} from today to today+days_ahead.

    Walks day by day so the dashboard calendar can colour cells by balance state.
    """
    if today is None:
        today = date.today()
    end = today + timedelta(days=days_ahead)
    starting = get_starting_balance(account_ids)
    instances = expand_bills(today, end, account_ids)

    # Group instances by date for efficient lookup
    by_date = {}
    for i in instances:
        d = i['date']
        by_date.setdefault(d, []).append(i)

    balances = {}
    running = starting
    cursor = today
    while cursor <= end:
        if cursor in by_date:
            for ev in by_date[cursor]:
                running += ev['amount']
        balances[cursor.isoformat()] = round(running, 2)
        cursor += timedelta(days=1)

    return balances, starting, instances
