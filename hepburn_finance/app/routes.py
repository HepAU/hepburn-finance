"""Flask routes for Hepburn Finance dashboard."""
import os
import logging
from datetime import date, datetime, timedelta
from flask import Blueprint, render_template, request, jsonify, redirect, url_for

from app.database import get_db, get_setting, set_setting
from app.parsers import parse_csv
from app.categoriser import categorise_batch
from app.forecast import forecast_daily_balances, expand_bills, expand_transfers, parse_iso
from app.stress import compute_stress, debt_attack_order
from app.suggestions import smart_suggestions
from app.notifications import notify

logger = logging.getLogger(__name__)

bp = Blueprint('main', __name__)


def _selected_account_ids():
    """Read 'selected_accounts' from settings.
    Default: all transaction & savings accounts."""
    raw = get_setting('selected_accounts', '')
    if raw:
        try:
            return [int(x) for x in raw.split(',') if x.strip()]
        except ValueError:
            pass
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id FROM accounts WHERE archived=0 AND type IN ('transaction','savings')"
        ).fetchall()
    return [r['id'] for r in rows]


def _all_categories():
    """Distinct categories from rules + scheduled bills + transfers + transactions.
    Sorted alphabetically — used to populate the autocomplete datalist."""
    cats = set()
    with get_db() as conn:
        for r in conn.execute('SELECT DISTINCT category FROM category_rules').fetchall():
            if r['category']:
                cats.add(r['category'])
        for r in conn.execute('SELECT DISTINCT category FROM scheduled_bills WHERE category IS NOT NULL').fetchall():
            if r['category']:
                cats.add(r['category'])
        for r in conn.execute('SELECT DISTINCT category FROM scheduled_transfers WHERE category IS NOT NULL').fetchall():
            if r['category']:
                cats.add(r['category'])
        for r in conn.execute("SELECT DISTINCT category FROM transactions WHERE category IS NOT NULL AND category != 'Uncategorised'").fetchall():
            if r['category']:
                cats.add(r['category'])
    return sorted(cats, key=str.lower)


def _validate_future_date(date_str):
    """For one-off bills, reject past dates. For recurring, allow."""
    try:
        d = parse_iso(date_str)
        return d, None
    except (ValueError, TypeError):
        return None, 'Invalid date format'


# Bendigo reference number → account name mapping for auto-categorisation.
# When Peta moves money between sub-accounts without entering a description,
# Bendigo populates the description with the destination account number.
# This map lets us turn those into readable transfers.
BENDIGO_INTERNAL_REFS = {
    '00571644691402': 'Income & Bills Account',
    '00571644691403': 'Rainy Day Funds',
    '00571644691404': 'Other Peoples Money',
    '00571644691405': 'Holiday Funds',
}


def _auto_detect_transfers(conn):
    """Pair-match internal transfers across the user's accounts.

    Two passes:
    1. Bendigo reference-number heuristic: descriptions matching one of the
       known sub-account refs are tagged as internal regardless of pairing.
    2. Pair-matching: same date + same magnitude + opposite signs +
       different accounts = an internal transfer pair.

    Returns count of matched pairs.
    """
    matched_pairs = 0

    # Pass 1: Bendigo reference codes
    for ref_code, target_name in BENDIGO_INTERNAL_REFS.items():
        conn.execute(
            "UPDATE transactions SET is_internal_transfer=1, "
            "category='Transfer · Internal', user_categorised=1, "
            "updated_at=datetime('now') "
            "WHERE description LIKE ? AND (is_internal_transfer=0 OR is_internal_transfer IS NULL)",
            (f'%{ref_code}%',)
        )

    # Pass 2: pair-match by date + magnitude across accounts
    rows = conn.execute(
        "SELECT id, account_id, date, amount FROM transactions "
        "WHERE transfer_pair_id IS NULL"
    ).fetchall()

    from collections import defaultdict
    buckets = defaultdict(list)
    for r in rows:
        key = (r['date'], round(abs(r['amount']), 2))
        buckets[key].append(r)

    matched_ids = set()
    for key, candidates in buckets.items():
        for i, a in enumerate(candidates):
            if a['id'] in matched_ids:
                continue
            for b in candidates[i+1:]:
                if b['id'] in matched_ids:
                    continue
                if (a['account_id'] != b['account_id']
                        and ((a['amount'] > 0 and b['amount'] < 0)
                             or (a['amount'] < 0 and b['amount'] > 0))):
                    conn.execute(
                        "UPDATE transactions SET is_internal_transfer=1, "
                        "transfer_pair_id=?, category='Transfer · Internal', "
                        "user_categorised=1, updated_at=datetime('now') "
                        "WHERE id=?",
                        (b['id'], a['id'])
                    )
                    conn.execute(
                        "UPDATE transactions SET is_internal_transfer=1, "
                        "transfer_pair_id=?, category='Transfer · Internal', "
                        "user_categorised=1, updated_at=datetime('now') "
                        "WHERE id=?",
                        (a['id'], b['id'])
                    )
                    matched_ids.add(a['id'])
                    matched_ids.add(b['id'])
                    matched_pairs += 1
                    break

    return matched_pairs


@bp.route('/')
def dashboard():
    today = date.today()
    selected_ids = _selected_account_ids()

    # Account display priority — frequency of use, not alphabetical type.
    # Most-actioned everyday accounts surface first; reference accounts last.
    TYPE_PRIORITY = {
        'transaction': 1,   # Card, Income & Bills — checked daily
        'credit':      2,   # Gem Visa — checked weekly
        'savings':     3,   # Sub-accounts — referenced sometimes
        'loan_informal': 4, # Keith, PVRSC — referenced when paying back
        'loan_personal': 5, # Solar, car loans — fixed schedule
        'ppor':        6,   # Owner-occupier mortgage — reference
        'loan_investment': 7,  # Investment loans — reference
        'loan':        8,   # Generic legacy
    }

    with get_db() as conn:
        all_accounts_raw = conn.execute(
            "SELECT * FROM accounts WHERE archived=0 ORDER BY bank, name"
        ).fetchall()
        recent_tx = conn.execute(
            "SELECT t.*, a.name AS account_name "
            "FROM transactions t JOIN accounts a ON t.account_id = a.id "
            "WHERE t.is_internal_transfer = 0 OR t.is_internal_transfer IS NULL "
            "ORDER BY t.date DESC, t.id DESC LIMIT 10"
        ).fetchall()
        plans = conn.execute(
            "SELECT * FROM interest_free_plans ORDER BY expiry_date ASC"
        ).fetchall()

    # Hydrate accounts with computed balances + last-updated dates
    from app.balances import hydrate_accounts
    all_accounts = hydrate_accounts(all_accounts_raw)

    # Re-sort by (bank, type-priority, name) using our priority map
    all_accounts.sort(key=lambda a: (
        a['bank'] or '',
        TYPE_PRIORITY.get(a['type'], 99),
        a['name'] or '',
    ))

    # Account-id → name map (for transfer rendering)
    account_name = {a['id']: a['name'] for a in all_accounts}

    accounts_by_bank = {}
    for a in all_accounts:
        accounts_by_bank.setdefault(a['bank'], []).append(a)

    # Order bank groups by the most-used account type they contain.
    # A bank with a transaction account beats a bank with only mortgages.
    accounts_by_bank = dict(sorted(
        accounts_by_bank.items(),
        key=lambda kv: (
            min((TYPE_PRIORITY.get(a['type'], 99) for a in kv[1]), default=99),
            kv[0] or '',
        )
    ))

    balances, starting_bal, instances_60d = forecast_daily_balances(
        selected_ids, days_ahead=60, today=today
    )

    # Upcoming bills (14d) — bills only, not transfers, for the right-column list.
    bills_14d = expand_bills(today, today + timedelta(days=14), selected_ids)

    # Upcoming transfers (14d) — for a separate panel
    transfers_14d = expand_transfers(today, today + timedelta(days=14), selected_ids)
    # Filter transfers where neither account is selected
    transfers_14d = [t for t in transfers_14d if t['net_effect'] is not None]

    stress = compute_stress(selected_ids, today)
    suggestions = smart_suggestions(selected_ids, today)
    debt = debt_attack_order()

    # Cash total uses display_balance (which respects manual `available`
    # override on transaction/credit accounts), so it matches what the user
    # sees on the cards. For savings accounts that's just computed_balance.
    cash_total = sum(
        a['display_balance'] for a in all_accounts
        if a['type'] in ('transaction', 'savings')
    )
    debt_total = sum(
        a['computed_balance'] for a in all_accounts
        if a['type'] in ('loan_investment', 'loan_personal', 'loan_informal', 'ppor', 'loan')
    )
    credit_total = sum(a['computed_balance'] for a in all_accounts if a['type'] == 'credit')
    redraw_total = sum(
        (a['available_redraw'] or 0) for a in all_accounts
        if a['type'] in ('loan_investment', 'loan_personal', 'ppor', 'loan')
    )

    # Budgets status — for the Spending budgets card
    from app.budgets import budget_status
    with get_db() as conn:
        budget_rows = conn.execute(
            "SELECT b.*, a.name AS account_name, a.bank AS account_bank "
            "FROM spending_budgets b "
            "LEFT JOIN accounts a ON b.account_id = a.id "
            "WHERE b.active = 1 "
            "ORDER BY b.cadence, b.name"
        ).fetchall()
    budgets_status = [budget_status(dict(b), today=today) for b in budget_rows]

    return render_template(
        'dashboard.html',
        today=today.isoformat(),
        today_obj=today,
        today_str=today.strftime('%A, %d %B %Y'),
        accounts_by_bank=accounts_by_bank,
        account_name=account_name,
        selected_ids=set(selected_ids),
        recent_tx=[dict(t) for t in recent_tx],
        plans=[dict(p) for p in plans],
        balances=balances,
        starting_bal=starting_bal,
        bills_14d=bills_14d,
        transfers_14d=transfers_14d,
        instances_60d=instances_60d,
        stress=stress,
        suggestions=suggestions,
        debt=debt,
        cash_total=cash_total,
        debt_total=debt_total,
        credit_total=credit_total,
        redraw_total=redraw_total,
        seed_data_present=_detect_seed_data()['has_any'],
        budgets_status=budgets_status,
    )


@bp.route('/api/toggle-account', methods=['POST'])
def toggle_account():
    aid = int(request.json.get('account_id'))
    selected = _selected_account_ids()
    if aid in selected:
        selected.remove(aid)
    else:
        selected.append(aid)
    set_setting('selected_accounts', ','.join(str(s) for s in selected))
    return jsonify({'selected': selected})


# ---------- Accounts ----------

@bp.route('/accounts/new', methods=['GET', 'POST'])
def new_account():
    if request.method == 'POST':
        opening_bal = request.form.get('opening_balance')
        if opening_bal is None or opening_bal == '':
            return 'Opening balance is required', 400
        opening_bal = float(opening_bal)

        with get_db() as conn:
            conn.execute(
                'INSERT INTO accounts (bank, name, nickname, account_number, type, '
                'balance, opening_balance, balance_last_updated, '
                'available, available_redraw, credit_limit, interest_rate, is_deductible, notes) '
                'VALUES (?,?,?,?,?,?,?,datetime(\'now\'),?,?,?,?,?,?)',
                (
                    request.form.get('bank', '').strip() or 'Other',
                    request.form.get('name', '').strip(),
                    request.form.get('nickname', '').strip() or None,
                    request.form.get('account_number', '').strip() or None,
                    request.form.get('type', 'transaction'),
                    opening_bal,  # legacy `balance` matches opening at creation
                    opening_bal,
                    float(request.form.get('available')) if request.form.get('available') else None,
                    float(request.form.get('available_redraw')) if request.form.get('available_redraw') else None,
                    float(request.form.get('credit_limit')) if request.form.get('credit_limit') else None,
                    float(request.form.get('interest_rate')) if request.form.get('interest_rate') else None,
                    1 if request.form.get('is_deductible') else 0,
                    request.form.get('notes', '').strip() or None,
                ),
            )
        return redirect(url_for('main.dashboard'))
    return render_template('account_form.html', account=None, today_iso=date.today().isoformat())


@bp.route('/accounts/<int:aid>/edit', methods=['GET', 'POST'])
def edit_account(aid):
    with get_db() as conn:
        acc_row = conn.execute('SELECT * FROM accounts WHERE id=?', (aid,)).fetchone()
        if not acc_row:
            return 'Not found', 404

        if request.method == 'POST':
            # Override opening balance flow (advanced — hidden in <details>)
            new_opening = request.form.get('new_opening_balance')
            opening_as_of = request.form.get('opening_as_of') or date.today().isoformat()
            if new_opening:
                try:
                    new_opening_val = float(new_opening)
                    # Wipe transactions ON OR BEFORE the as-of date.
                    # Semantics: "balance is correct at end of <opening_as_of>",
                    # so transactions on that day or earlier are already baked
                    # into the new opening figure. Only later transactions roll forward.
                    conn.execute(
                        'DELETE FROM transactions WHERE account_id=? AND date<=?',
                        (aid, opening_as_of)
                    )
                    conn.execute(
                        'UPDATE accounts SET opening_balance=?, balance_last_updated=datetime(\'now\') '
                        'WHERE id=?',
                        (new_opening_val, aid)
                    )
                except ValueError:
                    pass

            conn.execute(
                'UPDATE accounts SET bank=?, name=?, nickname=?, account_number=?, '
                'type=?, available=?, available_redraw=?, credit_limit=?, '
                'interest_rate=?, is_deductible=?, notes=?, updated_at=datetime(\'now\') WHERE id=?',
                (
                    request.form.get('bank', '').strip() or 'Other',
                    request.form.get('name', '').strip(),
                    request.form.get('nickname', '').strip() or None,
                    request.form.get('account_number', '').strip() or None,
                    request.form.get('type', 'transaction'),
                    float(request.form.get('available')) if request.form.get('available') else None,
                    float(request.form.get('available_redraw')) if request.form.get('available_redraw') else None,
                    float(request.form.get('credit_limit')) if request.form.get('credit_limit') else None,
                    float(request.form.get('interest_rate')) if request.form.get('interest_rate') else None,
                    1 if request.form.get('is_deductible') else 0,
                    request.form.get('notes', '').strip() or None,
                    aid,
                ),
            )
            return redirect(url_for('main.dashboard'))

    # Hydrate the single account so the form can show computed_balance
    from app.balances import hydrate_accounts
    hydrated = hydrate_accounts([acc_row])
    return render_template('account_form.html', account=hydrated[0] if hydrated else dict(acc_row), today_iso=date.today().isoformat())


@bp.route('/accounts/<int:aid>/delete', methods=['POST'])
def delete_account(aid):
    with get_db() as conn:
        conn.execute('UPDATE accounts SET archived=1 WHERE id=?', (aid,))
    return redirect(url_for('main.dashboard'))


# ---------- Bills ----------

@bp.route('/bills/new', methods=['GET', 'POST'])
def new_bill():
    if request.method == 'POST':
        amt = float(request.form['amount'])
        is_income = request.form.get('type') == 'income'
        amt = abs(amt) if is_income else -abs(amt)

        next_d, err = _validate_future_date(request.form['next_date'])
        if err:
            return err, 400

        # Block past one-off bills
        recurring = request.form.get('recurring', 'monthly')
        if recurring == 'once' and next_d < date.today():
            return 'One-off bills cannot be in the past', 400

        end_date = request.form.get('end_date') or None
        occurrences = request.form.get('occurrences_remaining')

        with get_db() as conn:
            conn.execute(
                'INSERT INTO scheduled_bills (name, amount, next_date, recurring, '
                'end_date, occurrences_remaining, category, account_id, is_income) '
                'VALUES (?,?,?,?,?,?,?,?,?)',
                (
                    request.form['name'].strip(),
                    amt,
                    request.form['next_date'],
                    recurring,
                    end_date,
                    int(occurrences) if occurrences else None,
                    request.form.get('category', '').strip() or None,
                    int(request.form['account_id']),
                    1 if is_income else 0,
                )
            )
        return redirect(url_for('main.dashboard'))

    with get_db() as conn:
        accounts = conn.execute(
            "SELECT id, name, bank FROM accounts WHERE archived=0 ORDER BY bank, name"
        ).fetchall()
    prefilled_date = request.args.get('date', '')
    return render_template(
        'bill_form.html',
        bill=None,
        accounts=accounts,
        categories=_all_categories(),
        prefilled_date=prefilled_date,
        today_iso=date.today().isoformat(),
    )


@bp.route('/bills/<int:bid>/edit', methods=['GET', 'POST'])
def edit_bill(bid):
    with get_db() as conn:
        bill = conn.execute('SELECT * FROM scheduled_bills WHERE id=?', (bid,)).fetchone()
        if not bill:
            return 'Not found', 404
        if request.method == 'POST':
            amt = float(request.form['amount'])
            is_income = request.form.get('type') == 'income'
            amt = abs(amt) if is_income else -abs(amt)

            recurring = request.form.get('recurring', 'monthly')
            end_date = request.form.get('end_date') or None
            occurrences = request.form.get('occurrences_remaining')

            conn.execute(
                'UPDATE scheduled_bills SET name=?, amount=?, next_date=?, recurring=?, '
                'end_date=?, occurrences_remaining=?, category=?, account_id=?, is_income=?, '
                'updated_at=datetime(\'now\') WHERE id=?',
                (
                    request.form['name'].strip(),
                    amt,
                    request.form['next_date'],
                    recurring,
                    end_date,
                    int(occurrences) if occurrences else None,
                    request.form.get('category', '').strip() or None,
                    int(request.form['account_id']),
                    1 if is_income else 0,
                    bid,
                )
            )
            return redirect(url_for('main.dashboard'))
        accounts = conn.execute(
            "SELECT id, name, bank FROM accounts WHERE archived=0 ORDER BY bank, name"
        ).fetchall()
    return render_template(
        'bill_form.html',
        bill=dict(bill),
        accounts=accounts,
        categories=_all_categories(),
        prefilled_date='',
        today_iso=date.today().isoformat(),
    )


@bp.route('/bills/<int:bid>/delete', methods=['POST'])
def delete_bill(bid):
    with get_db() as conn:
        conn.execute('DELETE FROM scheduled_bills WHERE id=?', (bid,))
    return redirect(url_for('main.dashboard'))


# ---------- Afterpay shortcut ----------

@bp.route('/afterpay/new', methods=['GET', 'POST'])
def new_afterpay():
    """Quick form: total amount + first instalment date + store + account.
    Creates 4 fortnightly bills as a single fixed series.
    Each instalment is a separate one-off bill so they show distinctly on the calendar.
    """
    if request.method == 'POST':
        store = request.form['store'].strip()
        total = float(request.form['total'])
        first_date_str = request.form['first_date']
        account_id = int(request.form['account_id'])
        instalments = int(request.form.get('instalments', 4))

        first_date, err = _validate_future_date(first_date_str)
        if err:
            return err, 400

        per_instalment = round(total / instalments, 2)
        # Adjust last instalment so the rounded sum matches the total exactly
        last_instalment = round(total - per_instalment * (instalments - 1), 2)

        from datetime import timedelta as _td
        with get_db() as conn:
            for i in range(instalments):
                instal_date = first_date + _td(days=14 * i)
                amt = per_instalment if i < instalments - 1 else last_instalment
                conn.execute(
                    'INSERT INTO scheduled_bills (name, amount, next_date, recurring, '
                    'category, account_id, is_income) '
                    'VALUES (?,?,?,?,?,?,?)',
                    (
                        f'Afterpay · {store} ({i+1} of {instalments})',
                        -abs(amt),
                        instal_date.isoformat(),
                        'once',
                        'Buy Now Pay Later · Afterpay',
                        account_id,
                        0,
                    )
                )
        return redirect(url_for('main.dashboard'))

    with get_db() as conn:
        accounts = conn.execute(
            "SELECT id, name, bank FROM accounts WHERE archived=0 ORDER BY bank, name"
        ).fetchall()
    return render_template(
        'afterpay_form.html',
        accounts=accounts,
        today_iso=date.today().isoformat(),
    )






# ---------- Transfers ----------

@bp.route('/transfers/new', methods=['GET', 'POST'])
def new_transfer():
    if request.method == 'POST':
        from_id = int(request.form['from_account_id'])
        to_id = int(request.form['to_account_id'])
        if from_id == to_id:
            return 'Source and destination must differ', 400

        recurring = request.form.get('recurring', 'monthly')
        if recurring == 'once':
            d, _ = _validate_future_date(request.form['next_date'])
            if d and d < date.today():
                return 'One-off transfers cannot be in the past', 400

        end_date = request.form.get('end_date') or None
        occurrences = request.form.get('occurrences_remaining')

        with get_db() as conn:
            conn.execute(
                'INSERT INTO scheduled_transfers (name, amount, next_date, recurring, '
                'end_date, occurrences_remaining, from_account_id, to_account_id, category, notes) '
                'VALUES (?,?,?,?,?,?,?,?,?,?)',
                (
                    request.form['name'].strip(),
                    abs(float(request.form['amount'])),
                    request.form['next_date'],
                    recurring,
                    end_date,
                    int(occurrences) if occurrences else None,
                    from_id,
                    to_id,
                    request.form.get('category', '').strip() or None,
                    request.form.get('notes', '').strip() or None,
                )
            )
        return redirect(url_for('main.dashboard'))

    with get_db() as conn:
        accounts = conn.execute(
            "SELECT id, name, bank, type FROM accounts WHERE archived=0 ORDER BY bank, type, name"
        ).fetchall()
    prefilled_date = request.args.get('date', '')
    return render_template(
        'transfer_form.html',
        transfer=None,
        accounts=accounts,
        categories=_all_categories(),
        prefilled_date=prefilled_date,
        today_iso=date.today().isoformat(),
    )


@bp.route('/transfers/<int:tid>/edit', methods=['GET', 'POST'])
def edit_transfer(tid):
    with get_db() as conn:
        transfer = conn.execute('SELECT * FROM scheduled_transfers WHERE id=?', (tid,)).fetchone()
        if not transfer:
            return 'Not found', 404
        if request.method == 'POST':
            from_id = int(request.form['from_account_id'])
            to_id = int(request.form['to_account_id'])
            if from_id == to_id:
                return 'Source and destination must differ', 400

            recurring = request.form.get('recurring', 'monthly')
            end_date = request.form.get('end_date') or None
            occurrences = request.form.get('occurrences_remaining')

            conn.execute(
                'UPDATE scheduled_transfers SET name=?, amount=?, next_date=?, recurring=?, '
                'end_date=?, occurrences_remaining=?, from_account_id=?, to_account_id=?, '
                'category=?, notes=?, updated_at=datetime(\'now\') WHERE id=?',
                (
                    request.form['name'].strip(),
                    abs(float(request.form['amount'])),
                    request.form['next_date'],
                    recurring,
                    end_date,
                    int(occurrences) if occurrences else None,
                    from_id,
                    to_id,
                    request.form.get('category', '').strip() or None,
                    request.form.get('notes', '').strip() or None,
                    tid,
                )
            )
            return redirect(url_for('main.dashboard'))
        accounts = conn.execute(
            "SELECT id, name, bank, type FROM accounts WHERE archived=0 ORDER BY bank, type, name"
        ).fetchall()
    return render_template(
        'transfer_form.html',
        transfer=dict(transfer),
        accounts=accounts,
        categories=_all_categories(),
        prefilled_date='',
        today_iso=date.today().isoformat(),
    )


@bp.route('/transfers/<int:tid>/delete', methods=['POST'])
def delete_transfer(tid):
    with get_db() as conn:
        conn.execute('DELETE FROM scheduled_transfers WHERE id=?', (tid,))
    return redirect(url_for('main.dashboard'))


# ---------- Transactions ----------

@bp.route('/transactions')
def list_transactions():
    """Browse transactions with filtering/searching."""
    q = request.args.get('q', '').strip()
    cat = request.args.get('cat', '').strip()
    aid = request.args.get('account', '').strip()
    uncat = request.args.get('uncat', '').strip() == '1'

    # Build SQL with a LEFT JOIN through transfer_pair_id to the matching half,
    # then JOIN to that half's account. Gives us pair_account_name = the OTHER
    # side of the transfer (source if this row is the credit, destination if
    # this row is the debit).
    sql = ("SELECT t.*, a.name AS account_name, "
           "pair_acc.name AS pair_account_name, "
           "pair_acc.bank AS pair_account_bank "
           "FROM transactions t "
           "JOIN accounts a ON t.account_id = a.id "
           "LEFT JOIN transactions pair_t ON pair_t.id = t.transfer_pair_id "
           "LEFT JOIN accounts pair_acc ON pair_acc.id = pair_t.account_id "
           "WHERE 1=1")
    params = []
    if q:
        sql += " AND (t.description LIKE ? OR t.raw_description LIKE ?)"
        params.extend([f'%{q}%', f'%{q}%'])
    if cat:
        sql += " AND t.category = ?"
        params.append(cat)
    if aid:
        try:
            sql += " AND t.account_id = ?"
            params.append(int(aid))
        except ValueError:
            pass
    if uncat:
        # Show only transactions with no category, blank category, or default
        # 'Uncategorised', AND not yet manually tagged by the user.
        sql += (" AND (t.category IS NULL OR t.category = '' OR t.category = 'Uncategorised') "
                "AND (t.user_categorised = 0 OR t.user_categorised IS NULL)")
    # Sort to match Bendigo mobile app's order:
    #   - Newest day at the top (date DESC)
    #   - Within each day, oldest transaction first, newest last (id ASC)
    # This way the dashboard's same-day order reads top-to-bottom in the same
    # direction as the bank app — easier reconciliation. The id-asc tie-breaker
    # works because CSV imports preserve chronological order in import-id.
    sql += " ORDER BY t.date DESC, t.id ASC LIMIT 200"

    with get_db() as conn:
        txs = conn.execute(sql, params).fetchall()
        accounts = conn.execute(
            "SELECT id, name, bank FROM accounts WHERE archived=0 ORDER BY bank, name"
        ).fetchall()
        # Count of uncategorised transactions overall (for the toggle button label)
        uncat_count = conn.execute(
            "SELECT COUNT(*) FROM transactions "
            "WHERE (category IS NULL OR category = '' OR category = 'Uncategorised') "
            "AND (user_categorised = 0 OR user_categorised IS NULL)"
        ).fetchone()[0]

    return render_template(
        'transactions.html',
        transactions=[dict(t) for t in txs],
        accounts=accounts,
        categories=_all_categories(),
        q=q, cat=cat, aid=aid, uncat=uncat,
        uncat_count=uncat_count,
    )


# ---------- Spending budget management ----------

@bp.route('/budgets')
def list_budgets():
    """Show all spending budgets with current period status."""
    from app.budgets import get_active_budgets, budget_status
    with get_db() as conn:
        budgets_raw = conn.execute(
            "SELECT b.*, a.name AS account_name, a.bank AS account_bank "
            "FROM spending_budgets b "
            "LEFT JOIN accounts a ON b.account_id = a.id "
            "ORDER BY b.active DESC, b.cadence, b.name"
        ).fetchall()
    today = date.today()
    budgets_with_status = []
    for b in budgets_raw:
        d = dict(b)
        if d['active']:
            d['status'] = budget_status(d, today=today)
        else:
            d['status'] = None
        budgets_with_status.append(d)
    return render_template('budgets.html', budgets=budgets_with_status, today=today.isoformat())


@bp.route('/budgets/new', methods=['GET', 'POST'])
def new_budget():
    if request.method == 'POST':
        with get_db() as conn:
            conn.execute(
                "INSERT INTO spending_budgets (name, category, amount, cadence, "
                "account_id, notes, active) VALUES (?,?,?,?,?,?,1)",
                (
                    request.form.get('name', '').strip(),
                    request.form.get('category', '').strip(),
                    float(request.form.get('amount') or 0),
                    request.form.get('cadence', 'weekly'),
                    int(request.form['account_id']) if request.form.get('account_id') else None,
                    request.form.get('notes', '').strip() or None,
                ),
            )
        return redirect(url_for('main.list_budgets'))

    with get_db() as conn:
        accounts = conn.execute(
            "SELECT id, name, bank, type FROM accounts WHERE archived=0 "
            "AND type IN ('transaction','credit') ORDER BY bank, name"
        ).fetchall()
    # Pre-fill from query string if a "Set cap" suggestion is sending us here
    prefill = {
        'name': request.args.get('name', '').strip(),
        'category': request.args.get('category', '').strip(),
        'amount': request.args.get('amount', '').strip(),
        'cadence': request.args.get('cadence', 'weekly').strip(),
    }
    return render_template(
        'budget_form.html',
        budget=None,
        accounts=[dict(a) for a in accounts],
        categories=_all_categories(),
        prefill=prefill,
    )


@bp.route('/budgets/<int:bid>/edit', methods=['GET', 'POST'])
def edit_budget(bid):
    with get_db() as conn:
        budget = conn.execute(
            'SELECT * FROM spending_budgets WHERE id=?', (bid,)
        ).fetchone()
        if not budget:
            return 'Not found', 404

        if request.method == 'POST':
            conn.execute(
                "UPDATE spending_budgets SET name=?, category=?, amount=?, "
                "cadence=?, account_id=?, notes=?, active=?, "
                "updated_at=datetime('now') WHERE id=?",
                (
                    request.form.get('name', '').strip(),
                    request.form.get('category', '').strip(),
                    float(request.form.get('amount') or 0),
                    request.form.get('cadence', 'weekly'),
                    int(request.form['account_id']) if request.form.get('account_id') else None,
                    request.form.get('notes', '').strip() or None,
                    1 if request.form.get('active') else 0,
                    bid,
                ),
            )
            return redirect(url_for('main.list_budgets'))

        accounts = conn.execute(
            "SELECT id, name, bank, type FROM accounts WHERE archived=0 "
            "AND type IN ('transaction','credit') ORDER BY bank, name"
        ).fetchall()
    return render_template(
        'budget_form.html',
        budget=dict(budget),
        accounts=[dict(a) for a in accounts],
        categories=_all_categories(),
        prefill={},
    )


@bp.route('/budgets/<int:bid>/delete', methods=['POST'])
def delete_budget(bid):
    with get_db() as conn:
        conn.execute('DELETE FROM spending_budgets WHERE id=?', (bid,))
    return redirect(url_for('main.list_budgets'))


# ---------- Interest-free plan management ----------

@bp.route('/plans')
def list_plans():
    """List all interest-free plans across credit accounts."""
    with get_db() as conn:
        plans = conn.execute(
            "SELECT p.*, a.name AS account_name, a.bank "
            "FROM interest_free_plans p "
            "JOIN accounts a ON p.account_id = a.id "
            "ORDER BY p.expiry_date ASC"
        ).fetchall()
    return render_template('plans.html', plans=[dict(p) for p in plans])


@bp.route('/plans/new', methods=['GET', 'POST'])
def new_plan():
    if request.method == 'POST':
        with get_db() as conn:
            conn.execute(
                "INSERT INTO interest_free_plans (account_id, name, detail, "
                "starting_balance, current_balance, monthly_payment, expiry_date, expired_rate) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    int(request.form['account_id']),
                    request.form.get('name', '').strip(),
                    request.form.get('detail', '').strip() or None,
                    float(request.form.get('starting_balance') or 0),
                    float(request.form.get('current_balance') or 0),
                    float(request.form.get('monthly_payment')) if request.form.get('monthly_payment') else None,
                    request.form.get('expiry_date', '').strip(),
                    float(request.form.get('expired_rate') or 29.99),
                ),
            )
        return redirect(url_for('main.list_plans'))

    with get_db() as conn:
        # Plans only attach to credit-card accounts
        accounts = conn.execute(
            "SELECT id, name, bank FROM accounts WHERE archived=0 AND type='credit' "
            "ORDER BY bank, name"
        ).fetchall()
    return render_template(
        'plan_form.html',
        plan=None,
        accounts=[dict(a) for a in accounts],
    )


@bp.route('/plans/<int:pid>/edit', methods=['GET', 'POST'])
def edit_plan(pid):
    with get_db() as conn:
        plan = conn.execute(
            'SELECT * FROM interest_free_plans WHERE id=?', (pid,)
        ).fetchone()
        if not plan:
            return 'Not found', 404

        if request.method == 'POST':
            conn.execute(
                "UPDATE interest_free_plans SET name=?, detail=?, "
                "starting_balance=?, current_balance=?, monthly_payment=?, "
                "expiry_date=?, expired_rate=? WHERE id=?",
                (
                    request.form.get('name', '').strip(),
                    request.form.get('detail', '').strip() or None,
                    float(request.form.get('starting_balance') or 0),
                    float(request.form.get('current_balance') or 0),
                    float(request.form.get('monthly_payment')) if request.form.get('monthly_payment') else None,
                    request.form.get('expiry_date', '').strip(),
                    float(request.form.get('expired_rate') or 29.99),
                    pid,
                ),
            )
            return redirect(url_for('main.list_plans'))

        accounts = conn.execute(
            "SELECT id, name, bank FROM accounts WHERE archived=0 AND type='credit' "
            "ORDER BY bank, name"
        ).fetchall()
    return render_template(
        'plan_form.html',
        plan=dict(plan),
        accounts=[dict(a) for a in accounts],
    )


@bp.route('/plans/<int:pid>/delete', methods=['POST'])
def delete_plan(pid):
    with get_db() as conn:
        conn.execute('DELETE FROM interest_free_plans WHERE id=?', (pid,))
    return redirect(url_for('main.list_plans'))


@bp.route('/transactions/new', methods=['GET', 'POST'])
def new_transaction():
    """Manually add a single transaction.

    Useful for accounts where CSV import isn't available (e.g. Latitude
    Gem Visa, which only provides on-screen statements). The transaction is
    fingerprinted and treated like any imported one — it'll affect the
    computed balance of the account it's posted to.
    """
    if request.method == 'POST':
        try:
            account_id = int(request.form['account_id'])
            tx_date = request.form.get('date', '').strip() or date.today().isoformat()
            amount = float(request.form['amount'])
            sign = request.form.get('sign', 'debit')
            if sign == 'debit' and amount > 0:
                amount = -amount
            elif sign == 'credit' and amount < 0:
                amount = abs(amount)
            description = request.form.get('description', '').strip() or 'Manual entry'
            category = request.form.get('category', '').strip() or None
            notes = request.form.get('notes', '').strip() or None
        except (ValueError, KeyError) as e:
            return f'Invalid form data: {e}', 400

        # Fingerprint identical to importer to allow dedup against future CSV imports
        import hashlib
        fp_input = f'{account_id}|{tx_date}|{amount}|{description}|manual'
        fingerprint = hashlib.sha256(fp_input.encode()).hexdigest()

        with get_db() as conn:
            try:
                conn.execute(
                    "INSERT INTO transactions (account_id, date, amount, description, "
                    "raw_description, category, notes, fingerprint, user_categorised, "
                    "is_internal_transfer) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (account_id, tx_date, amount, description, description, category,
                     notes, fingerprint, 1 if category else 0, 0)
                )
            except Exception as e:
                return f'Could not save: {e}', 400

        return redirect(url_for('main.list_transactions'))

    # GET — render the form
    with get_db() as conn:
        accounts = conn.execute(
            "SELECT id, name, type, bank FROM accounts WHERE archived=0 "
            "ORDER BY bank, type, name"
        ).fetchall()
    return render_template(
        'transaction_form.html',
        tx=None,
        accounts=[dict(a) for a in accounts],
        categories=_all_categories(),
        similar=[],
        today=date.today().isoformat(),
    )


@bp.route('/transactions/<int:tid>/edit', methods=['GET', 'POST'])
def edit_transaction(tid):
    with get_db() as conn:
        tx = conn.execute(
            "SELECT t.*, a.name AS account_name, "
            "(SELECT account_id FROM transactions WHERE id = t.transfer_pair_id) AS transfer_pair_account_id "
            "FROM transactions t "
            "JOIN accounts a ON t.account_id = a.id WHERE t.id=?",
            (tid,)
        ).fetchone()
        if not tx:
            return 'Not found', 404

        if request.method == 'POST':
            new_category = request.form.get('category', '').strip() or None
            new_description = request.form.get('description', '').strip()
            new_amount = float(request.form['amount'])
            new_notes = request.form.get('notes', '').strip() or None
            new_is_internal = 1 if request.form.get('is_internal_transfer') else 0
            destination_account_id = request.form.get('destination_account_id', '').strip()
            destination_account_id = int(destination_account_id) if destination_account_id else None

            conn.execute(
                "UPDATE transactions SET category=?, description=?, amount=?, "
                "notes=?, is_internal_transfer=?, user_categorised=1, "
                "updated_at=datetime('now') WHERE id=?",
                (new_category, new_description, new_amount, new_notes, new_is_internal, tid)
            )

            # Counterpart logic: if marked as internal transfer AND user picked a
            # destination, ensure a paired transaction exists on that account with
            # the opposite-sign amount. Used for cases where the destination
            # account doesn't get a CSV import (e.g. informal loans, accounts
            # outside the user's bank).
            existing_pair_id = tx['transfer_pair_id']
            import hashlib

            def _make_counterpart(dest_id, amt, desc, src_account_name, tx_date, tx_id):
                """Create the matching transaction on the destination account."""
                fp_input = f'pair|{tx_id}|{dest_id}|{tx_date}|{-amt}|{desc}'
                fingerprint = hashlib.sha256(fp_input.encode()).hexdigest()
                cur = conn.execute(
                    "INSERT INTO transactions (account_id, date, amount, description, "
                    "raw_description, category, notes, fingerprint, user_categorised, "
                    "is_internal_transfer, transfer_pair_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (dest_id, tx_date, -amt, f'Transfer from {src_account_name}',
                     desc, 'Transfer · Internal', None, fingerprint, 1, 1, tx_id)
                )
                return cur.lastrowid

            if new_is_internal and destination_account_id:
                src_name = tx['account_name']

                if existing_pair_id:
                    # Update the existing counterpart — but only if its account
                    # matches the chosen destination. Otherwise: delete + recreate.
                    pair = conn.execute(
                        'SELECT id, account_id FROM transactions WHERE id=?',
                        (existing_pair_id,)
                    ).fetchone()
                    if pair and pair['account_id'] == destination_account_id:
                        conn.execute(
                            "UPDATE transactions SET amount=?, "
                            "description=?, raw_description=?, "
                            "updated_at=datetime('now') WHERE id=?",
                            (-new_amount, f'Transfer from {src_name}', new_description, existing_pair_id)
                        )
                    else:
                        # Destination changed — delete old counterpart, create new
                        if pair:
                            conn.execute('DELETE FROM transactions WHERE id=?', (existing_pair_id,))
                        new_pair_id = _make_counterpart(
                            destination_account_id, new_amount, new_description,
                            src_name, tx['date'], tid
                        )
                        conn.execute(
                            "UPDATE transactions SET transfer_pair_id=? WHERE id=?",
                            (new_pair_id, tid)
                        )
                else:
                    # Create the counterpart
                    new_pair_id = _make_counterpart(
                        destination_account_id, new_amount, new_description,
                        src_name, tx['date'], tid
                    )
                    conn.execute(
                        "UPDATE transactions SET transfer_pair_id=? WHERE id=?",
                        (new_pair_id, tid)
                    )
            elif not new_is_internal and existing_pair_id:
                # User unchecked internal transfer — clean up the orphan counterpart
                conn.execute('DELETE FROM transactions WHERE id=?', (existing_pair_id,))
                conn.execute(
                    "UPDATE transactions SET transfer_pair_id=NULL WHERE id=?",
                    (tid,)
                )

            # Bulk apply: did they ask to apply this category to similar transactions?
            apply_to_similar = request.form.get('apply_to_similar')
            if apply_to_similar and new_category:
                # Match other uncategorised transactions with similar description
                old_desc = tx['description']
                # Use a robust LIKE match — strip down to first non-numeric word for matching
                like_pattern = '%' + (old_desc.split()[0] if old_desc else old_desc) + '%'
                cur = conn.execute(
                    "UPDATE transactions SET category=?, user_categorised=1, "
                    "updated_at=datetime('now') "
                    "WHERE id != ? AND description LIKE ? "
                    "AND (category IS NULL OR category='Uncategorised' OR user_categorised=0)",
                    (new_category, tid, like_pattern)
                )
                affected = cur.rowcount
                if affected > 0:
                    conn.execute(
                        "INSERT INTO notifications_log (kind, title, body) VALUES (?,?,?)",
                        ('bulk_categorise', f'Tagged {affected} similar transactions',
                         f'Pattern: "{like_pattern}" → {new_category}')
                    )

            # Preserve any list filters the user came from (account, category, search, uncat)
            filter_args = {}
            for key in ('account', 'cat', 'q', 'uncat'):
                val = request.form.get(f'_filter_{key}', '').strip()
                if val:
                    filter_args[key] = val
            return redirect(url_for('main.list_transactions', **filter_args))

    # Find similar untagged transactions to offer bulk-tag
    similar = []
    if tx['description']:
        first_word = tx['description'].split()[0] if tx['description'] else ''
        if first_word:
            with get_db() as conn:
                similar = conn.execute(
                    "SELECT id, description, amount, date FROM transactions "
                    "WHERE id != ? AND description LIKE ? "
                    "AND (category IS NULL OR category='Uncategorised' OR user_categorised=0) "
                    "ORDER BY date DESC LIMIT 5",
                    (tid, f'%{first_word}%')
                ).fetchall()

    # Accounts list for the destination-account dropdown (for transfers)
    with get_db() as conn:
        all_accounts = conn.execute(
            "SELECT id, name, bank, type FROM accounts WHERE archived=0 "
            "AND id != ? ORDER BY bank, type, name",
            (tx['account_id'],)
        ).fetchall()

    # Capture filter args from URL so the edit form can pass them through
    filter_args = {
        'account': request.args.get('account', '').strip(),
        'cat': request.args.get('cat', '').strip(),
        'q': request.args.get('q', '').strip(),
        'uncat': request.args.get('uncat', '').strip(),
    }

    return render_template(
        'transaction_form.html',
        tx=dict(tx),
        categories=_all_categories(),
        similar=[dict(s) for s in similar],
        accounts=[dict(a) for a in all_accounts],
        filter_args=filter_args,
    )


@bp.route('/transactions/<int:tid>/delete', methods=['POST'])
def delete_transaction(tid):
    """Delete a transaction. If it's a paired transfer, cascade to the other half."""
    with get_db() as conn:
        # Find the pair (if any) before deleting
        pair_row = conn.execute(
            'SELECT transfer_pair_id FROM transactions WHERE id=?', (tid,)
        ).fetchone()
        pair_id = pair_row['transfer_pair_id'] if pair_row else None

        conn.execute('DELETE FROM transactions WHERE id=?', (tid,))
        if pair_id:
            conn.execute('DELETE FROM transactions WHERE id=?', (pair_id,))
    # Preserve filters from the originating list view (passed as form fields)
    filter_args = {}
    for key in ('account', 'cat', 'q', 'uncat'):
        val = request.form.get(f'_filter_{key}', '').strip()
        if val:
            filter_args[key] = val
    return redirect(url_for('main.list_transactions', **filter_args))


@bp.route('/transactions/detect-transfers', methods=['POST'])
def detect_internal_transfers():
    """Pair-match internal transfers across the user's accounts.

    Looks for transactions with same date, opposite-sign amounts of the
    same magnitude, in different accounts. Marks both legs as
    is_internal_transfer=1, links them via transfer_pair_id, and tags both
    with category 'Transfer · Internal'.

    Also handles the Bendigo case where descriptions are just numeric
    reference codes — they don't need matching since they're already known
    to be internal moves.
    """
    matched_pairs = 0
    with get_db() as conn:
        # First pass: pair-match by date + magnitude across accounts
        rows = conn.execute(
            "SELECT id, account_id, date, amount, description "
            "FROM transactions "
            "WHERE is_internal_transfer=0 OR is_internal_transfer IS NULL"
        ).fetchall()

        # Index by (date, abs_amount) for quick pair-finding
        from collections import defaultdict
        buckets = defaultdict(list)
        for r in rows:
            key = (r['date'], round(abs(r['amount']), 2))
            buckets[key].append(r)

        for key, candidates in buckets.items():
            # Need at least one positive and one negative in different accounts
            for i, a in enumerate(candidates):
                for b in candidates[i+1:]:
                    if (a['account_id'] != b['account_id']
                            and ((a['amount'] > 0 and b['amount'] < 0)
                                 or (a['amount'] < 0 and b['amount'] > 0))):
                        # It's a match
                        conn.execute(
                            "UPDATE transactions SET is_internal_transfer=1, "
                            "transfer_pair_id=?, category='Transfer · Internal', "
                            "user_categorised=1, updated_at=datetime('now') "
                            "WHERE id IN (?, ?)",
                            (b['id'], a['id'], b['id'])
                        )
                        # Set b's pair to a
                        conn.execute(
                            "UPDATE transactions SET transfer_pair_id=? WHERE id=?",
                            (a['id'], b['id'])
                        )
                        matched_pairs += 1
                        break  # Only match each transaction once

    return jsonify({'matched_pairs': matched_pairs})


@bp.route('/accounts/<int:aid>/reconcile')
def reconcile_account_view(aid):
    """Show a reconciliation report for an account: manual balance vs tx sum."""
    with get_db() as conn:
        acc = conn.execute('SELECT * FROM accounts WHERE id=?', (aid,)).fetchone()
        if not acc:
            return 'Not found', 404
        tx_sum = conn.execute(
            'SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE account_id=? '
            'AND (is_internal_transfer=0 OR is_internal_transfer IS NULL)',
            (aid,)
        ).fetchone()[0]
        tx_sum_with_internal = conn.execute(
            'SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE account_id=?',
            (aid,)
        ).fetchone()[0]
        first_tx = conn.execute(
            'SELECT date, amount FROM transactions WHERE account_id=? ORDER BY date ASC LIMIT 1',
            (aid,)
        ).fetchone()
        last_tx = conn.execute(
            'SELECT date FROM transactions WHERE account_id=? ORDER BY date DESC LIMIT 1',
            (aid,)
        ).fetchone()
        tx_count = conn.execute(
            'SELECT COUNT(*) FROM transactions WHERE account_id=?',
            (aid,)
        ).fetchone()[0]

    return render_template(
        'reconcile.html',
        account=dict(acc),
        tx_sum=round(tx_sum, 2),
        tx_sum_with_internal=round(tx_sum_with_internal, 2),
        tx_count=tx_count,
        first_tx_date=first_tx['date'] if first_tx else None,
        last_tx_date=last_tx['date'] if last_tx else None,
        difference=round(acc['balance'] - tx_sum, 2),
    )


@bp.route('/transactions/reconcile/<int:aid>')
def reconcile_account(aid):
    """Compare manual balance to transaction-derived balance for an account."""
    with get_db() as conn:
        acc = conn.execute('SELECT * FROM accounts WHERE id=?', (aid,)).fetchone()
        if not acc:
            return 'Not found', 404
        tx_sum = conn.execute(
            'SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE account_id=?',
            (aid,)
        ).fetchone()[0]
        first_tx = conn.execute(
            'SELECT date, amount FROM transactions WHERE account_id=? '
            'ORDER BY date ASC LIMIT 1',
            (aid,)
        ).fetchone()
        last_tx = conn.execute(
            'SELECT date FROM transactions WHERE account_id=? '
            'ORDER BY date DESC LIMIT 1',
            (aid,)
        ).fetchone()

    return jsonify({
        'account_id': aid,
        'name': acc['name'],
        'manual_balance': acc['balance'],
        'tx_sum': round(tx_sum, 2),
        'difference': round(acc['balance'] - tx_sum, 2),
        'first_tx_date': first_tx['date'] if first_tx else None,
        'last_tx_date': last_tx['date'] if last_tx else None,
    })




# ---------- Upload ----------

@bp.route('/upload', methods=['GET', 'POST'])
def upload():
    if request.method == 'POST':
        file = request.files.get('file')
        account_id = request.form.get('account_id')
        if not file or not account_id:
            return 'Missing file or account', 400

        try:
            content = file.read().decode('utf-8-sig')
        except UnicodeDecodeError:
            try:
                content = file.read().decode('latin-1')
            except Exception:
                return 'Could not decode file', 400

        txs, parser_used = parse_csv(content, int(account_id))
        categorise_batch(
            txs,
            ai_provider=os.environ.get('AI_PROVIDER', 'none'),
            ai_api_key=os.environ.get('AI_API_KEY', ''),
        )

        new_count = 0
        with get_db() as conn:
            for tx in txs:
                try:
                    conn.execute(
                        'INSERT INTO transactions (account_id, date, amount, description, '
                        'raw_description, transaction_type, reference, category, fingerprint) '
                        'VALUES (?,?,?,?,?,?,?,?,?)',
                        (
                            tx['account_id'], tx['date'], tx['amount'],
                            tx['description'], tx['raw_description'], tx['transaction_type'],
                            tx['reference'], tx.get('category', 'Uncategorised'),
                            tx['fingerprint'],
                        )
                    )
                    new_count += 1
                except Exception:
                    pass

            # Run pair-matching to auto-detect internal transfers across accounts
            transfer_pairs = _auto_detect_transfers(conn)

        return render_template(
            'upload_result.html',
            total=len(txs),
            new_count=new_count,
            duplicates=len(txs) - new_count,
            parser=parser_used,
            account_id=account_id,
            transfer_pairs=transfer_pairs,
        )

    with get_db() as conn:
        accounts = conn.execute(
            "SELECT id, name, bank FROM accounts WHERE archived=0 ORDER BY bank, name"
        ).fetchall()
    return render_template('upload.html', accounts=accounts)


# ---------- API ----------

@bp.route('/api/day-details')
def api_day_details():
    """Return all transactions on a specific date for the calendar's day-click popover."""
    day = request.args.get('date', '')
    if not day:
        return jsonify({'transactions': []})
    selected_ids = _selected_account_ids()
    if not selected_ids:
        return jsonify({'transactions': []})
    placeholders = ','.join('?' * len(selected_ids))
    with get_db() as conn:
        rows = conn.execute(
            f"SELECT t.id, t.date, t.amount, t.description, t.category, "
            f"t.is_internal_transfer, a.name AS account_name "
            f"FROM transactions t JOIN accounts a ON t.account_id = a.id "
            f"WHERE t.account_id IN ({placeholders}) AND t.date = ? "
            f"AND (t.is_internal_transfer = 0 OR t.is_internal_transfer IS NULL) "
            f"ORDER BY ABS(t.amount) DESC",
            (*selected_ids, day)
        ).fetchall()
    return jsonify({
        'transactions': [
            {
                'id': r['id'],
                'amount': r['amount'],
                'description': r['description'],
                'category': r['category'] or 'Uncategorised',
                'account_name': r['account_name'],
            }
            for r in rows
        ]
    })


@bp.route('/api/forecast')
def api_forecast():
    """Return forecast data for a date range, used by the calendar."""
    today = date.today()
    days = int(request.args.get('days', 90))
    selected_ids = _selected_account_ids()
    balances, starting, instances = forecast_daily_balances(
        selected_ids, days_ahead=days, today=today
    )

    # Past transactions in the visible window for the calendar
    past_window_start = (today - timedelta(days=60)).isoformat()
    with get_db() as conn:
        if selected_ids:
            placeholders = ','.join('?' * len(selected_ids))
            past_rows = conn.execute(
                f"SELECT date, COUNT(*) as count, SUM(amount) as total "
                f"FROM transactions "
                f"WHERE account_id IN ({placeholders}) "
                f"AND date >= ? AND date < ? "
                f"AND (is_internal_transfer = 0 OR is_internal_transfer IS NULL) "
                f"GROUP BY date",
                (*selected_ids, past_window_start, today.isoformat())
            ).fetchall()
        else:
            past_rows = []
        past_summary = {r['date']: {'count': r['count'], 'total': round(r['total'], 2)}
                        for r in past_rows}

    return jsonify({
        'balances': balances,
        'starting': starting,
        'past_summary': past_summary,
        'instances': [
            {
                'date': i['date'].isoformat(),
                'name': i['name'],
                'amount': i['amount'],
                'category': i.get('category'),
                'is_income': i.get('is_income', False),
                'recurring': i.get('recurring'),
                'id': i.get('id'),
                'kind': i.get('kind', 'bill'),
                'from_account_id': i.get('from_account_id'),
                'to_account_id': i.get('to_account_id'),
                'net_effect': i.get('net_effect'),
            }
            for i in instances
        ],
    })


@bp.route('/notify-test')
def notify_test():
    """Trigger a test HA notification."""
    target = os.environ.get('NOTIFY_SERVICE') or None
    ok = notify('Hepburn Finance', 'Test notification — wiring works.', target=target)
    return jsonify({'sent': ok, 'target': target or 'persistent_notification'})


@bp.route('/api/ha-refresh', methods=['POST', 'GET'])
def api_ha_refresh():
    """Manually trigger a push of all finance sensors to HA.
    Useful after big data changes (CSV upload, bulk edits)."""
    try:
        from app.ha_sensors import compute_and_push_all
        ok = compute_and_push_all()
        return jsonify({'pushed': ok})
    except Exception as e:
        logger.exception('HA refresh failed')
        return jsonify({'pushed': False, 'error': str(e)}), 500


# ---------- One-shot cleanup of v0.1.x demo seed data ----------
# In v0.1.0 the add-on seeded demo accounts + interest-free plans on first
# install. From v0.5.1 onwards installs are empty by default. Existing users
# can use this page to remove the seed data left over from earlier installs.

SEED_ACCOUNT_NAMES = {
    'Card Account', 'Income & Bills Account', 'Rainy Day Funds',
    'Other Peoples Money', 'Holiday Funds', 'Tax Account',
    'Mortgage Loan (PPOR)', 'Robina Mortgage', 'Nundah Mortgage',
    'Gem Visa',
}

SEED_PLAN_NAMES = {
    'Penrith Auto', 'Pandora purchase', 'December purchase', 'February purchase',
    'Harvey Norman (electrical)', 'Harvey Norman (computer)',
    'Amazon (small)', 'Amazon (larger)',
}


def _detect_seed_data():
    """Return a dict describing seed data still present in the DB.

    Once the user has run the cleanup tool, we set seed_cleanup_completed
    to '1' in settings. After that, has_any will always return False so
    the banner stays hidden — even if the user chose to keep some seeded
    accounts (rename them to use them as real accounts, etc.). They can
    still revisit /admin/cleanup directly.
    """
    if get_setting('seed_cleanup_completed') == '1':
        return {'accounts': [], 'plans': [], 'has_any': False, 'cleanup_completed': True}

    with get_db() as conn:
        if not SEED_ACCOUNT_NAMES:
            accounts = []
        else:
            accounts = conn.execute(
                "SELECT id, name, bank, type, balance, opening_balance "
                "FROM accounts WHERE name IN ({}) AND archived=0".format(
                    ','.join('?' * len(SEED_ACCOUNT_NAMES))
                ),
                tuple(SEED_ACCOUNT_NAMES)
            ).fetchall()

        if not SEED_PLAN_NAMES:
            plans = []
        else:
            plans = conn.execute(
                "SELECT id, name, current_balance, expiry_date "
                "FROM interest_free_plans WHERE name IN ({})".format(
                    ','.join('?' * len(SEED_PLAN_NAMES))
                ),
                tuple(SEED_PLAN_NAMES)
            ).fetchall()

        # Per-account transaction counts so user can see what's there
        tx_counts = {}
        for a in accounts:
            cnt = conn.execute(
                'SELECT COUNT(*) FROM transactions WHERE account_id=?',
                (a['id'],)
            ).fetchone()[0]
            tx_counts[a['id']] = cnt

    return {
        'accounts': [dict(a, tx_count=tx_counts.get(a['id'], 0)) for a in accounts],
        'plans': [dict(p) for p in plans],
        'has_any': len(accounts) > 0 or len(plans) > 0,
    }


@bp.route('/admin/cleanup', methods=['GET', 'POST'])
def admin_cleanup():
    if request.method == 'POST':
        confirm = request.form.get('confirm', '').strip()
        if confirm != 'DELETE':
            return ('Confirmation phrase did not match. '
                    'Type "DELETE" exactly to proceed. '
                    '<a href="/admin/cleanup">Try again</a>'), 400

        seed = _detect_seed_data()
        removed_accounts, wiped_tx_accounts, removed_plans = [], [], []

        with get_db() as conn:
            for a in seed['accounts']:
                action = request.form.get(f'action_{a["id"]}', 'keep')
                if action == 'remove':
                    conn.execute('DELETE FROM transactions WHERE account_id=?', (a['id'],))
                    conn.execute('DELETE FROM interest_free_plans WHERE account_id=?', (a['id'],))
                    conn.execute('DELETE FROM scheduled_bills WHERE account_id=?', (a['id'],))
                    conn.execute(
                        'DELETE FROM scheduled_transfers '
                        'WHERE from_account_id=? OR to_account_id=?',
                        (a['id'], a['id'])
                    )
                    conn.execute('DELETE FROM accounts WHERE id=?', (a['id'],))
                    removed_accounts.append(a['name'])
                elif action == 'wipe_tx':
                    conn.execute('DELETE FROM transactions WHERE account_id=?', (a['id'],))
                    conn.execute(
                        "UPDATE accounts SET balance_last_updated=datetime('now') "
                        "WHERE id=?",
                        (a['id'],)
                    )
                    wiped_tx_accounts.append(a['name'])

            for p in seed['plans']:
                action = request.form.get(f'plan_{p["id"]}', 'keep')
                if action == 'remove':
                    conn.execute('DELETE FROM interest_free_plans WHERE id=?', (p['id'],))
                    removed_plans.append(p['name'])

        # Mark cleanup as completed — banner will no longer show, even if the
        # user chose "Leave alone" for some items. They can still visit
        # /admin/cleanup directly to manage seed data later.
        set_setting('seed_cleanup_completed', '1')

        return render_template(
            'admin_cleanup_done.html',
            removed_accounts=removed_accounts,
            wiped_tx_accounts=wiped_tx_accounts,
            removed_plans=removed_plans,
        )

    # GET — show the cleanup form
    # If the user already completed cleanup, show empty state unless ?force=1
    force = request.args.get('force') == '1'
    if get_setting('seed_cleanup_completed') == '1' and not force:
        # Force-detect (bypass the flag) so user can see what's still around
        seed = {'accounts': [], 'plans': [], 'has_any': False, 'cleanup_completed': True}
    else:
        # Bypass the flag for this view
        with get_db() as conn:
            accounts = conn.execute(
                "SELECT id, name, bank, type, balance, opening_balance "
                "FROM accounts WHERE name IN ({}) AND archived=0".format(
                    ','.join('?' * len(SEED_ACCOUNT_NAMES))
                ),
                tuple(SEED_ACCOUNT_NAMES)
            ).fetchall() if SEED_ACCOUNT_NAMES else []

            plans = conn.execute(
                "SELECT id, name, current_balance, expiry_date "
                "FROM interest_free_plans WHERE name IN ({})".format(
                    ','.join('?' * len(SEED_PLAN_NAMES))
                ),
                tuple(SEED_PLAN_NAMES)
            ).fetchall() if SEED_PLAN_NAMES else []

            tx_counts = {}
            for a in accounts:
                cnt = conn.execute(
                    'SELECT COUNT(*) FROM transactions WHERE account_id=?',
                    (a['id'],)
                ).fetchone()[0]
                tx_counts[a['id']] = cnt

        seed = {
            'accounts': [dict(a, tx_count=tx_counts.get(a['id'], 0)) for a in accounts],
            'plans': [dict(p) for p in plans],
            'has_any': len(accounts) > 0 or len(plans) > 0,
            'cleanup_completed': get_setting('seed_cleanup_completed') == '1',
        }
    return render_template('admin_cleanup.html', seed=seed)


@bp.route('/ha-dashboard')
def ha_dashboard_help():
    """Show the Lovelace card setup help page."""
    import os as _os
    has_token = bool(_os.environ.get('SUPERVISOR_TOKEN'))
    return render_template('ha_dashboard.html', has_token=has_token)


@bp.route('/health')
def health():
    return jsonify({'status': 'ok'})
