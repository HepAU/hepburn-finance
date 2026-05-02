"""Spending budgets — weekly/fortnightly/monthly category caps.

A budget is a recurring spend allowance for a specific category. Each budget:
- has a target account (so the forecast knows which balance to drain)
- has a cadence (weekly/fortnightly/monthly)
- resets at the start of each period

For weekly: period = Mon-Sun (Australian/ISO convention).
For fortnightly: period = 14 days starting from a fixed reference Monday.
For monthly: period = calendar month (1st to last day).

The forecast applies budgets as a smooth daily drain across the remaining days
of each period: `daily_drain = max(0, amount - actual_spent_this_period) / days_remaining`.

Real transactions in the budget's category that have already happened this period
"consume" the budget — so we don't double-count between projected and actual spend.
"""
from datetime import date, timedelta

from app.database import get_db


# Reference Monday for fortnightly calculations — any historical Monday works.
# Using 2024-01-01 (a Monday) as the fortnightly anchor.
FORTNIGHT_ANCHOR = date(2024, 1, 1)


def period_for_date(d, cadence):
    """Return (period_start, period_end) for the given date and cadence.

    Both bounds are inclusive. period_end is the last day of the period.
    """
    if cadence == 'weekly':
        # Monday = 0 in weekday()
        offset = d.weekday()
        start = d - timedelta(days=offset)
        end = start + timedelta(days=6)
        return start, end
    if cadence == 'fortnightly':
        days_since_anchor = (d - FORTNIGHT_ANCHOR).days
        period_index = days_since_anchor // 14
        start = FORTNIGHT_ANCHOR + timedelta(days=period_index * 14)
        end = start + timedelta(days=13)
        return start, end
    if cadence == 'monthly':
        start = d.replace(day=1)
        # Last day of month: jump to next month's day 1, subtract 1
        if start.month == 12:
            next_month = date(start.year + 1, 1, 1)
        else:
            next_month = date(start.year, start.month + 1, 1)
        end = next_month - timedelta(days=1)
        return start, end
    raise ValueError(f'Unknown cadence: {cadence}')


def get_active_budgets(conn=None):
    """Return all active budgets, joined with account info."""
    own_conn = conn is None
    if own_conn:
        conn = get_db().__enter__()
    try:
        rows = conn.execute(
            "SELECT b.*, a.name AS account_name, a.bank AS account_bank "
            "FROM spending_budgets b "
            "LEFT JOIN accounts a ON b.account_id = a.id "
            "WHERE b.active = 1 "
            "ORDER BY b.cadence, b.name"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        if own_conn:
            conn.close()


def spent_in_period(category, account_id, period_start, period_end, conn):
    """Sum negative-amount transactions in this category, this account, this period.

    Returns a positive number representing total spent.
    Excludes internal transfers — those aren't real spending.
    Account_id None means any account (rare).
    """
    sql = (
        "SELECT COALESCE(SUM(ABS(amount)), 0) FROM transactions "
        "WHERE category = ? "
        "AND amount < 0 "
        "AND date >= ? AND date <= ? "
        "AND (is_internal_transfer = 0 OR is_internal_transfer IS NULL)"
    )
    params = [category, period_start.isoformat(), period_end.isoformat()]
    if account_id is not None:
        sql += " AND account_id = ?"
        params.append(account_id)
    return conn.execute(sql, params).fetchone()[0]


def budget_status(budget, today=None):
    """For a single budget, compute current period status.

    Returns dict with: period_start, period_end, amount, spent, remaining,
    days_remaining (inclusive of today), pct_used, daily_drain.
    """
    if today is None:
        today = date.today()
    p_start, p_end = period_for_date(today, budget['cadence'])
    with get_db() as conn:
        spent = spent_in_period(
            budget['category'], budget['account_id'],
            p_start, p_end, conn
        )
    amount = float(budget['amount'])
    remaining = max(0.0, amount - spent)
    days_remaining = (p_end - today).days + 1
    if days_remaining < 1:
        days_remaining = 1
    pct = round(min(100, (spent / amount * 100) if amount > 0 else 0))
    daily_drain = remaining / days_remaining if days_remaining > 0 else 0
    return {
        'budget_id': budget['id'],
        'name': budget['name'],
        'category': budget['category'],
        'cadence': budget['cadence'],
        'account_id': budget['account_id'],
        'account_name': budget.get('account_name'),
        'period_start': p_start,
        'period_end': p_end,
        'amount': round(amount, 2),
        'spent': round(spent, 2),
        'remaining': round(remaining, 2),
        'days_remaining': days_remaining,
        'pct_used': pct,
        'daily_drain': round(daily_drain, 2),
        'over_budget': spent > amount,
    }


def projected_drain_for_day(budget, target_date, today=None):
    """How much should the forecast subtract from the target account on this day?

    Logic:
    - If target_date is in the past or today, return 0 (already in cash-today).
    - For each *future* day in a period, the per-day drain is the period's
      total budget evenly distributed across the period's days, capped so that
      the sum of future drains across the period equals the remaining budget
      for that period (not the full amount).

    This means:
    - Future periods (next week, etc): full amount evenly spread across all
      7/14/30 days.
    - Current period: future days get an even share that sums to "remaining"
      (the unspent portion), so the total projected for the period matches
      what the budget actually has left after current spending.
    """
    if today is None:
        today = date.today()
    if target_date <= today:
        return 0.0
    p_start, p_end = period_for_date(target_date, budget['cadence'])
    period_days = (p_end - p_start).days + 1
    even_per_day = float(budget['amount']) / period_days

    cur_p_start, cur_p_end = period_for_date(today, budget['cadence'])
    same_period = (p_start == cur_p_start)
    if not same_period:
        # Future period — full even distribution
        return even_per_day

    # Current period: cap projection at remaining, distributed across future days
    status = budget_status(budget, today=today)
    future_days = (p_end - today).days
    if future_days <= 0:
        return 0.0
    # If remaining > even_per_day * future_days, projection should still spread
    # evenly. If remaining < that, it must scale down so the sum equals remaining.
    even_future_total = even_per_day * future_days
    if status['remaining'] >= even_future_total:
        return even_per_day
    return status['remaining'] / future_days
