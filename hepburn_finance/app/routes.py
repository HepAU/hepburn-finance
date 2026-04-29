"""Flask routes for Hepburn Finance dashboard."""
import os
import logging
from datetime import date, datetime, timedelta
from flask import Blueprint, render_template, request, jsonify, redirect, url_for

from app.database import get_db, get_setting, set_setting
from app.parsers import parse_csv
from app.categoriser import categorise_batch
from app.forecast import forecast_daily_balances, expand_bills, expand_transfers, parse_iso
from app.stress import compute_stress, smart_transfer_suggestions, debt_attack_order
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


@bp.route('/')
def dashboard():
    today = date.today()
    selected_ids = _selected_account_ids()

    with get_db() as conn:
        all_accounts = conn.execute(
            "SELECT * FROM accounts WHERE archived=0 ORDER BY bank, type, name"
        ).fetchall()
        recent_tx = conn.execute(
            "SELECT t.*, a.name AS account_name "
            "FROM transactions t JOIN accounts a ON t.account_id = a.id "
            "ORDER BY t.date DESC, t.id DESC LIMIT 10"
        ).fetchall()
        plans = conn.execute(
            "SELECT * FROM interest_free_plans ORDER BY expiry_date ASC"
        ).fetchall()

    # Account-id → name map (for transfer rendering)
    account_name = {a['id']: a['name'] for a in all_accounts}

    accounts_by_bank = {}
    for a in all_accounts:
        accounts_by_bank.setdefault(a['bank'], []).append(dict(a))

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
    suggestions = smart_transfer_suggestions(selected_ids, today)
    debt = debt_attack_order()

    cash_total = sum(
        (a['available'] if a['available'] is not None else a['balance'])
        for a in all_accounts
        if a['type'] in ('transaction', 'savings')
    )
    debt_total = sum(a['balance'] for a in all_accounts if a['type'] in ('loan', 'ppor'))
    credit_total = sum(a['balance'] for a in all_accounts if a['type'] == 'credit')
    redraw_total = sum(
        (a['available_redraw'] or 0) for a in all_accounts
        if a['type'] in ('loan', 'ppor')
    )

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
        with get_db() as conn:
            conn.execute(
                'INSERT INTO accounts (bank, name, nickname, account_number, type, balance, '
                'available, available_redraw, credit_limit, interest_rate, is_deductible, notes) '
                'VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                (
                    request.form.get('bank', '').strip() or 'Other',
                    request.form.get('name', '').strip(),
                    request.form.get('nickname', '').strip() or None,
                    request.form.get('account_number', '').strip() or None,
                    request.form.get('type', 'transaction'),
                    float(request.form.get('balance') or 0),
                    float(request.form.get('available')) if request.form.get('available') else None,
                    float(request.form.get('available_redraw')) if request.form.get('available_redraw') else None,
                    float(request.form.get('credit_limit')) if request.form.get('credit_limit') else None,
                    float(request.form.get('interest_rate')) if request.form.get('interest_rate') else None,
                    1 if request.form.get('is_deductible') else 0,
                    request.form.get('notes', '').strip() or None,
                ),
            )
        return redirect(url_for('main.dashboard'))
    return render_template('account_form.html', account=None)


@bp.route('/accounts/<int:aid>/edit', methods=['GET', 'POST'])
def edit_account(aid):
    with get_db() as conn:
        acc = conn.execute('SELECT * FROM accounts WHERE id=?', (aid,)).fetchone()
        if not acc:
            return 'Not found', 404

        if request.method == 'POST':
            conn.execute(
                'UPDATE accounts SET bank=?, name=?, nickname=?, account_number=?, '
                'type=?, balance=?, available=?, available_redraw=?, credit_limit=?, '
                'interest_rate=?, is_deductible=?, notes=?, updated_at=datetime(\'now\') WHERE id=?',
                (
                    request.form.get('bank', '').strip() or 'Other',
                    request.form.get('name', '').strip(),
                    request.form.get('nickname', '').strip() or None,
                    request.form.get('account_number', '').strip() or None,
                    request.form.get('type', 'transaction'),
                    float(request.form.get('balance') or 0),
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
    return render_template('account_form.html', account=dict(acc))


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
                    # Most likely UNIQUE constraint on fingerprint — duplicate, skip silently
                    pass

        return render_template(
            'upload_result.html',
            total=len(txs),
            new_count=new_count,
            duplicates=len(txs) - new_count,
            parser=parser_used,
            account_id=account_id,
        )

    with get_db() as conn:
        accounts = conn.execute(
            "SELECT id, name, bank FROM accounts WHERE archived=0 ORDER BY bank, name"
        ).fetchall()
    return render_template('upload.html', accounts=accounts)


# ---------- API ----------

@bp.route('/api/forecast')
def api_forecast():
    """Return forecast data for a date range, used by the calendar."""
    today = date.today()
    days = int(request.args.get('days', 90))
    selected_ids = _selected_account_ids()
    balances, starting, instances = forecast_daily_balances(
        selected_ids, days_ahead=days, today=today
    )
    return jsonify({
        'balances': balances,
        'starting': starting,
        'instances': [
            {
                'date': i['date'].isoformat(),
                'name': i['name'],
                'amount': i['amount'],
                'category': i['category'],
                'is_income': i.get('is_income', False),
                'recurring': i['recurring'],
                'id': i['id'],
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


@bp.route('/health')
def health():
    return jsonify({'status': 'ok'})
