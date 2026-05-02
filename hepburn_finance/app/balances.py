"""Account balance computation.

Once an account has an `opening_balance` set, its current balance is computed
as `opening_balance + sum(non-internal transactions since opening_balance was
set)`. This is the v0.4.0 model — transactions drive the balance.

Internal transfers are excluded from the sum (they net to zero across the
user's own accounts, so counting them would double-up).

If `opening_balance` is null (which shouldn't happen post-migration but is
handled defensively), we fall back to the legacy `balance` column.

The `available` figure for transaction/credit accounts is also computed:
it's the lesser of the displayed balance and any `available` value the
user manually entered (which represents pending holds — the bank's view
of "what's actually spendable now").
"""
from app.database import get_db


def compute_account_balance(account_row, conn=None):
    """Return the current computed balance for a single account.

    `account_row` is a sqlite Row (dict-like) with at least: id, opening_balance,
    balance, type, balance_last_updated.
    `conn` may be passed to avoid opening a new connection inside a loop.
    """
    aid = account_row['id']
    opening = account_row['opening_balance']

    if opening is None:
        # Pre-migration / not initialised — fall back to manual balance
        return account_row['balance']

    # Sum non-internal transactions since opening
    if conn is None:
        with get_db() as conn2:
            tx_sum = conn2.execute(
                'SELECT COALESCE(SUM(amount), 0) FROM transactions '
                'WHERE account_id = ? '
                'AND (is_internal_transfer = 0 OR is_internal_transfer IS NULL)',
                (aid,)
            ).fetchone()[0]
    else:
        tx_sum = conn.execute(
            'SELECT COALESCE(SUM(amount), 0) FROM transactions '
            'WHERE account_id = ? '
            'AND (is_internal_transfer = 0 OR is_internal_transfer IS NULL)',
            (aid,)
        ).fetchone()[0]

    return round(opening + tx_sum, 2)


def hydrate_accounts(account_rows):
    """Add a 'computed_balance' field to each account row in a list.

    Returns a list of dicts (since sqlite Rows are immutable).
    Use this in places where we render accounts in templates.
    """
    out = []
    if not account_rows:
        return out

    with get_db() as conn:
        # Pull all transaction sums in one query to avoid N+1
        ids = [r['id'] for r in account_rows]
        placeholders = ','.join('?' * len(ids))
        rows = conn.execute(
            f'SELECT account_id, COALESCE(SUM(amount), 0) AS s '
            f'FROM transactions '
            f'WHERE account_id IN ({placeholders}) '
            f'AND (is_internal_transfer = 0 OR is_internal_transfer IS NULL) '
            f'GROUP BY account_id',
            ids
        ).fetchall()
        sums = {r['account_id']: r['s'] for r in rows}

        # Latest transaction date per account, for "Last updated" display
        date_rows = conn.execute(
            f'SELECT account_id, MAX(date) AS last_tx '
            f'FROM transactions '
            f'WHERE account_id IN ({placeholders}) '
            f'GROUP BY account_id',
            ids
        ).fetchall()
        latest = {r['account_id']: r['last_tx'] for r in date_rows}

    for r in account_rows:
        d = dict(r)
        opening = d.get('opening_balance')
        tx_sum = sums.get(d['id'], 0)
        has_transactions = d['id'] in sums and tx_sum != 0  # Has movements

        if opening is not None:
            d['computed_balance'] = round(opening + tx_sum, 2)
        else:
            d['computed_balance'] = d['balance']

        # Headline balance:
        # - For transaction/credit accounts WITH transactions, computed_balance
        #   is most accurate (transactions drive the truth in v0.4.0+ model).
        # - For transaction/credit accounts WITHOUT transactions yet, use the
        #   manual `available` figure if set — it's the user's freshest snapshot
        #   from the bank.
        # - Everything else uses computed_balance.
        if d['type'] in ('transaction', 'credit'):
            if has_transactions:
                d['display_balance'] = d['computed_balance']
            elif d.get('available') is not None:
                d['display_balance'] = d['available']
            else:
                d['display_balance'] = d['computed_balance']
        else:
            d['display_balance'] = d['computed_balance']

        d['latest_tx_date'] = latest.get(d['id'])
        out.append(d)
    return out
