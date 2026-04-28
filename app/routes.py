"""Flask routes for Hepburn Finance dashboard."""
import os
import logging
from datetime import date, datetime, timedelta
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash

from app.database import get_db, get_setting, set_setting
from app.parsers import parse_csv
from app.categoriser import categorise_batch, categorise_by_rules, add_user_rule
from app.forecast import forecast_daily_balances, expand_bills, parse_iso, get_starting_balance
from app.stress import compute_stress, smart_transfer_suggestions, debt_attack_order
from app.notifications import notify

logger = logging.getLogger(__name__)

bp = Blueprint('main', __name__)


def _selected_account_ids():
    """Read 'selected_accounts' from settings, return list of ints.
    Defaults to all transaction-type accounts."""
    raw = get_setting('selected_accounts', '')
    if raw:
        try:
            return [int(x) for x in raw.split(',') if x.strip()]
        except ValueError:
            pass
    # Default: all transaction & savings accounts
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id FROM accounts WHERE archived=0 AND type IN ('transaction','savings')"
        ).fetchall()
    return [r['id'] for r in rows]


def _config():
    """Configuration values from environment (HA add-on options)."""
    return {
        'family_name': os.environ.get('FAMILY_NAME', 'Hepburn'),
        'primary_user': os.environ.get('PRIMARY_USER', ''),
        'secondary_user': os.environ.get('SECONDARY_USER', ''),
        'ai_provider': os.environ.get('AI_PROVIDER', 'none'),
    }


@bp.route('/')
def dashboard():
    today = date.today()
    cfg = _config()
    selected_ids = _selected_account_ids()

    # Accounts grouped by bank
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

    accounts_by_bank = {}
    for a in all_accounts:
        accounts_by_bank.setdefault(a['bank'], []).append(dict(a))

    # Forecast for selected accounts
    balances, starting_bal, instances_30d = forecast_daily_balances(selected_ids, days_ahead=60, today=today)
    bills_14d = expand_bills(today, today + timedelta(days=14), selected_ids)

    # Stress meter
    stress = compute_stress(selected_ids, today)
    suggestions = smart_transfer_suggestions(selected_ids, today)
    debt = debt_attack_order()

    # Cash totals (transaction + savings only)
    cash_total = sum(a['balance'] for a in all_accounts
                     if a['type'] in ('transaction', 'savings'))
    debt_total = sum(a['balance'] for a in all_accounts
                     if a['type'] in ('loan', 'ppor'))
    credit_total = sum(a['balance'] for a in all_accounts
                       if a['type'] == 'credit')

    return render_template(
        'dashboard.html',
        cfg=cfg,
        today=today.isoformat(),
        today_obj=today,
        today_str=today.strftime('%A, %d %B %Y'),
        accounts_by_bank=accounts_by_bank,
        selected_ids=set(selected_ids),
        recent_tx=[dict(t) for t in recent_tx],
        plans=[dict(p) for p in plans],
        balances=balances,
        starting_bal=starting_bal,
        bills_14d=bills_14d,
        instances_30d=instances_30d,
        stress=stress,
        suggestions=suggestions,
        debt=debt,
        cash_total=cash_total,
        debt_total=debt_total,
        credit_total=credit_total,
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


@bp.route('/accounts/new', methods=['GET', 'POST'])
def new_account():
    if request.method == 'POST':
        with get_db() as conn:
            conn.execute(
                'INSERT INTO accounts (bank, name, nickname, account_number, type, balance, '
                'available, credit_limit, interest_rate, is_deductible, notes) '
                'VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                (
                    request.form.get('bank', '').strip() or 'Other',
                    request.form.get('name', '').strip(),
                    request.form.get('nickname', '').strip() or None,
                    request.form.get('account_number', '').strip() or None,
                    request.form.get('type', 'transaction'),
                    float(request.form.get('balance') or 0),
                    float(request.form.get('available')) if request.form.get('available') else None,
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
                'type=?, balance=?, available=?, credit_limit=?, interest_rate=?, '
                'is_deductible=?, notes=?, updated_at=datetime(\'now\') WHERE id=?',
                (
                    request.form.get('bank', '').strip() or 'Other',
                    request.form.get('name', '').strip(),
                    request.form.get('nickname', '').strip() or None,
                    request.form.get('account_number', '').strip() or None,
                    request.form.get('type', 'transaction'),
                    float(request.form.get('balance') or 0),
                    float(request.form.get('available')) if request.form.get('available') else None,
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


@bp.route('/bills/new', methods=['GET', 'POST'])
def new_bill():
    if request.method == 'POST':
        amt = float(request.form['amount'])
        if request.form.get('type') == 'bill':
            amt = -abs(amt)
        else:
            amt = abs(amt)
        with get_db() as conn:
            conn.execute(
                'INSERT INTO scheduled_bills (name, amount, next_date, recurring, '
                'category, account_id, is_income) VALUES (?,?,?,?,?,?,?)',
                (
                    request.form['name'].strip(),
                    amt,
                    request.form['next_date'],
                    request.form.get('recurring', 'monthly'),
                    request.form.get('category', '').strip() or None,
                    int(request.form['account_id']),
                    1 if request.form.get('type') == 'income' else 0,
                )
            )
        return redirect(url_for('main.dashboard'))

    with get_db() as conn:
        accounts = conn.execute(
            "SELECT id, name, bank FROM accounts WHERE archived=0 ORDER BY bank, name"
        ).fetchall()
    prefilled_date = request.args.get('date', '')
    return render_template('bill_form.html', bill=None, accounts=accounts,
                           prefilled_date=prefilled_date)


@bp.route('/bills/<int:bid>/edit', methods=['GET', 'POST'])
def edit_bill(bid):
    with get_db() as conn:
        bill = conn.execute('SELECT * FROM scheduled_bills WHERE id=?', (bid,)).fetchone()
        if not bill:
            return 'Not found', 404
        if request.method == 'POST':
            amt = float(request.form['amount'])
            if request.form.get('type') == 'bill':
                amt = -abs(amt)
            else:
                amt = abs(amt)
            conn.execute(
                'UPDATE scheduled_bills SET name=?, amount=?, next_date=?, recurring=?, '
                'category=?, account_id=?, is_income=?, updated_at=datetime(\'now\') WHERE id=?',
                (
                    request.form['name'].strip(),
                    amt,
                    request.form['next_date'],
                    request.form.get('recurring', 'monthly'),
                    request.form.get('category', '').strip() or None,
                    int(request.form['account_id']),
                    1 if request.form.get('type') == 'income' else 0,
                    bid,
                )
            )
            return redirect(url_for('main.dashboard'))
        accounts = conn.execute(
            "SELECT id, name, bank FROM accounts WHERE archived=0 ORDER BY bank, name"
        ).fetchall()
    return render_template('bill_form.html', bill=dict(bill), accounts=accounts,
                           prefilled_date='')


@bp.route('/bills/<int:bid>/delete', methods=['POST'])
def delete_bill(bid):
    with get_db() as conn:
        conn.execute('DELETE FROM scheduled_bills WHERE id=?', (bid,))
    return redirect(url_for('main.dashboard'))


@bp.route('/upload', methods=['GET', 'POST'])
def upload():
    if request.method == 'POST':
        file = request.files.get('file')
        account_id = request.form.get('account_id')
        if not file or not account_id:
            return 'Missing file or account', 400

        try:
            content = file.read().decode('utf-8-sig')  # handle BOM
        except UnicodeDecodeError:
            try:
                content = file.read().decode('latin-1')
            except Exception:
                return 'Could not decode file', 400

        txs, parser_used = parse_csv(content, int(account_id))

        # Categorise
        cfg = _config()
        categorise_batch(txs, ai_provider=cfg['ai_provider'], ai_api_key=os.environ.get('AI_API_KEY', ''))

        # Insert (ignoring duplicates)
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
                except Exception as e:
                    # Likely UNIQUE constraint on fingerprint — duplicate
                    pass

        return render_template('upload_result.html',
                               total=len(txs), new_count=new_count,
                               parser=parser_used, account_id=account_id)

    with get_db() as conn:
        accounts = conn.execute(
            "SELECT id, name, bank FROM accounts WHERE archived=0 ORDER BY bank, name"
        ).fetchall()
    return render_template('upload.html', accounts=accounts)


@bp.route('/api/forecast')
def api_forecast():
    """Return forecast data for a date range, used by the calendar."""
    today = date.today()
    days = int(request.args.get('days', 60))
    selected_ids = _selected_account_ids()
    balances, starting, instances = forecast_daily_balances(selected_ids, days_ahead=days, today=today)
    return jsonify({
        'balances': balances,
        'starting': starting,
        'instances': [
            {
                'date': i['date'].isoformat(),
                'name': i['name'],
                'amount': i['amount'],
                'category': i['category'],
                'is_income': i['is_income'],
                'recurring': i['recurring'],
                'id': i['id'],
            }
            for i in instances
        ],
    })


@bp.route('/notify-test')
def notify_test():
    """Trigger a test HA notification. Use to verify wiring."""
    target = os.environ.get('NOTIFY_SERVICE') or None
    ok = notify('Hepburn Finance', 'Test notification — wiring works.', target=target)
    return jsonify({'sent': ok, 'target': target or 'persistent_notification'})


@bp.route('/health')
def health():
    return jsonify({'status': 'ok'})
