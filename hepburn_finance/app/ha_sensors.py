"""Push finance summaries to Home Assistant as native sensor entities.

The add-on talks to HA via the Supervisor proxy at http://supervisor/core/api,
authenticated by the SUPERVISOR_TOKEN env var that HA injects automatically
when `homeassistant_api: true` in config.yaml.

Sensors created (all under sensor.hepburn_finance_*):
  - cash_today: spendable cash across selected accounts ($)
  - balance_30d_low: lowest forecast balance over the next 30 days ($)
  - days_until_zero: days until forecast balance crosses zero (or 30+)
  - stress_tier: green / amber / red
  - next_bill_amount, next_bill_name, next_bill_date, next_bill_days_away
  - bills_7d_total: sum of bills due in next 7 days
  - bills_14d_total: sum of bills due in next 14 days
  - upcoming_bills_count: count of bills in next 14 days
  - debt_total: total debt across all loan-type accounts
  - redraw_total: total redraw available across loans

Each is sent as a state update with an attributes payload that includes
extra detail useful in Lovelace cards (e.g. the upcoming bills sensor has
a list attribute with the next 5 bills).
"""
import os
import json
import logging
import threading
from datetime import date, timedelta
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError

from app.database import get_db, get_setting
from app.forecast import forecast_daily_balances, expand_bills
from app.stress import compute_stress

logger = logging.getLogger(__name__)

SUPERVISOR_TOKEN = os.environ.get('SUPERVISOR_TOKEN')
HA_API_BASE = 'http://supervisor/core/api'
SENSOR_PREFIX = 'sensor.hepburn_finance'


def _ha_request(method, path, payload=None):
    """Make a request to HA via the Supervisor proxy.

    Returns (status_code, body_text). On network error returns (None, str).
    """
    if not SUPERVISOR_TOKEN:
        return None, 'no SUPERVISOR_TOKEN'

    url = f'{HA_API_BASE}{path}'
    data = None
    if payload is not None:
        data = json.dumps(payload).encode('utf-8')

    req = urlrequest.Request(url, data=data, method=method)
    req.add_header('Authorization', f'Bearer {SUPERVISOR_TOKEN}')
    req.add_header('Content-Type', 'application/json')

    try:
        with urlrequest.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read().decode('utf-8')
    except HTTPError as e:
        return e.code, e.read().decode('utf-8', errors='replace')
    except URLError as e:
        return None, str(e)
    except Exception as e:
        return None, str(e)


def push_sensor(sensor_id, state, attributes=None):
    """Set a sensor's state in HA.

    sensor_id should NOT include the 'sensor.' prefix — it's added here.
    state is the state value (string or number).
    attributes is a dict of extra attributes; friendly_name and unit_of_measurement
    are common.
    """
    full_id = f'sensor.{sensor_id}'
    payload = {'state': str(state) if state is not None else 'unknown'}
    if attributes:
        payload['attributes'] = attributes
    status, body = _ha_request('POST', f'/states/{full_id}', payload)
    if status not in (200, 201):
        logger.warning('Failed to push %s: status=%s body=%s', full_id, status, body[:200])
    return status in (200, 201)


def _selected_account_ids():
    """Read 'selected_accounts' setting; default to transaction & savings."""
    raw = get_setting('selected_accounts', '')
    if raw:
        try:
            return [int(x) for x in raw.split(',') if x.strip()]
        except ValueError:
            pass
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id FROM accounts WHERE archived=0 "
            "AND type IN ('transaction','savings')"
        ).fetchall()
    return [r['id'] for r in rows]


def compute_and_push_all():
    """Compute the full finance summary and push all sensors to HA.

    Designed to be safe to call from the Flask request thread (returns
    quickly since each push is bounded by 10s timeout). For periodic
    refresh, use start_periodic_push.
    """
    if not SUPERVISOR_TOKEN:
        logger.info('No SUPERVISOR_TOKEN — skipping HA sensor push (this is fine if running outside HA)')
        return False

    today = date.today()
    selected_ids = _selected_account_ids()

    try:
        balances, starting, instances = forecast_daily_balances(
            selected_ids, days_ahead=30, today=today
        )
    except Exception as e:
        logger.exception('Forecast failed: %s', e)
        return False

    # Stress
    try:
        stress = compute_stress(selected_ids, today)
    except Exception:
        stress = {'tier': 'unknown', 'dtz': None, 'coverage': 0, 'message': ''}

    # Cash today
    push_sensor(
        'hepburn_finance_cash_today',
        round(starting, 2),
        {
            'friendly_name': 'Hepburn · Cash today',
            'unit_of_measurement': '$',
            'icon': 'mdi:cash',
            'account_count': len(selected_ids),
        }
    )

    # 30d low forecast
    if balances:
        low = min(balances.values())
        low_date = next((d for d, v in balances.items() if v == low), None)
        push_sensor(
            'hepburn_finance_balance_30d_low',
            round(low, 2),
            {
                'friendly_name': 'Hepburn · Lowest forecast (30d)',
                'unit_of_measurement': '$',
                'icon': 'mdi:trending-down',
                'low_date': low_date,
            }
        )

    # Days until zero
    push_sensor(
        'hepburn_finance_days_until_zero',
        stress.get('dtz') if stress.get('dtz') is not None else 30,
        {
            'friendly_name': 'Hepburn · Days until zero',
            'unit_of_measurement': 'days',
            'icon': 'mdi:calendar-clock',
            'is_unbounded': stress.get('dtz') is None,
        }
    )

    # Stress tier
    push_sensor(
        'hepburn_finance_stress_tier',
        stress.get('tier', 'unknown'),
        {
            'friendly_name': 'Hepburn · Cash flow status',
            'icon': {
                'green': 'mdi:check-circle',
                'amber': 'mdi:alert',
                'red': 'mdi:alert-octagon',
            }.get(stress.get('tier'), 'mdi:help-circle'),
            'message': stress.get('message', ''),
            'coverage_pct': stress.get('coverage', 0),
        }
    )

    # Upcoming bills (14d) — and transfers too, so the popup shows the full
    # picture of money leaving the selected accounts. Transfers between two
    # selected accounts net to zero (not shown), but a transfer where only the
    # source is selected (e.g. mortgage payment to an unselected mortgage
    # account) reduces the running balance and must appear.
    try:
        bills_14d = expand_bills(today, today + timedelta(days=14), selected_ids)
    except Exception:
        bills_14d = []
    try:
        from app.forecast import expand_transfers
        transfers_14d_raw = expand_transfers(today, today + timedelta(days=14), selected_ids)
    except Exception:
        transfers_14d_raw = []
    # Only include transfers with a non-zero net effect on the selected account set.
    # net_effect is signed: negative = money leaving, positive = money coming in.
    transfers_14d = [
        t for t in transfers_14d_raw
        if t.get('net_effect') is not None and t['net_effect'] != 0
    ]

    bills_only = [b for b in bills_14d if not b.get('is_income')]
    bills_7d = [b for b in bills_only if (b['date'] - today).days <= 7]

    push_sensor(
        'hepburn_finance_upcoming_bills_count',
        len(bills_only),
        {
            'friendly_name': 'Hepburn · Upcoming bills',
            'unit_of_measurement': 'bills',
            'icon': 'mdi:receipt-text-clock',
            'window_days': 14,
        }
    )
    push_sensor(
        'hepburn_finance_bills_7d_total',
        round(sum(abs(b['amount']) for b in bills_7d), 2),
        {
            'friendly_name': 'Hepburn · Bills due (7d)',
            'unit_of_measurement': '$',
            'icon': 'mdi:calendar-week',
            'count': len(bills_7d),
        }
    )
    push_sensor(
        'hepburn_finance_bills_14d_total',
        round(sum(abs(b['amount']) for b in bills_only), 2),
        {
            'friendly_name': 'Hepburn · Bills due (14d)',
            'unit_of_measurement': '$',
            'icon': 'mdi:calendar-month',
            'count': len(bills_only),
        }
    )

    # Group bills by relative-time chunks for the dashboard popup
    def _chunk_label(days_away):
        if days_away == 0:
            return 'Today'
        if days_away == 1:
            return 'Tomorrow'
        if days_away <= 7:
            return 'This week'
        if days_away <= 14:
            return 'Next week'
        return 'Later'

    # Combine bills + transfers (net-effect outflows) into one grouped list
    # for the popup. Transfers show as labelled rows with their direction.
    combined = []
    for b in bills_only[:25]:
        combined.append({
            'name': b['name'],
            'amount': round(abs(b['amount']), 2),
            'date': b['date'],
            'category': b.get('category', ''),
            'kind': 'bill',
        })
    for t in transfers_14d[:15]:
        # net_effect is what hits the selected accounts. Negative = outflow.
        # The popup is about cash leaving, so we report the absolute outflow.
        # Skip net-positive (incoming) transfers — those are essentially
        # internal credits and not the popup's job to surface.
        net = t.get('net_effect', 0)
        if net >= 0:
            continue
        combined.append({
            'name': t['name'],
            'amount': round(abs(net), 2),
            'date': t['date'],
            'category': t.get('category', 'Transfer'),
            'kind': 'transfer',
        })

    combined.sort(key=lambda x: x['date'])

    grouped = {}
    for item in combined:
        days_away = (item['date'] - today).days
        chunk = _chunk_label(days_away)
        grouped.setdefault(chunk, []).append({
            'name': item['name'],
            'amount': item['amount'],
            'date': item['date'].isoformat(),
            'days_away': days_away,
            'category': item['category'],
            'kind': item['kind'],
        })

    # Order the chunks (so HA template loop is consistent)
    chunk_order = ['Today', 'Tomorrow', 'This week', 'Next week', 'Later']
    bills_grouped = [
        {'label': c, 'bills': grouped[c], 'subtotal': round(sum(b['amount'] for b in grouped[c]), 2)}
        for c in chunk_order if c in grouped
    ]

    # Next single bill
    if bills_only:
        nxt = bills_only[0]  # already sorted by date
        days_away = (nxt['date'] - today).days
        push_sensor(
            'hepburn_finance_next_bill_amount',
            round(abs(nxt['amount']), 2),
            {
                'friendly_name': 'Hepburn · Next bill',
                'unit_of_measurement': '$',
                'icon': 'mdi:receipt-text',
                'name': nxt['name'],
                'date': nxt['date'].isoformat(),
                'days_away': days_away,
                'category': nxt.get('category', ''),
                # Flat bills_list (kept for backward-compat, used by simpler cards)
                'bills_list': [
                    {
                        'name': b['name'],
                        'amount': round(abs(b['amount']), 2),
                        'date': b['date'].isoformat(),
                        'days_away': (b['date'] - today).days,
                        'category': b.get('category', ''),
                    }
                    for b in bills_only[:8]
                ],
                # Grouped chunks for the rich popup
                'bills_grouped': bills_grouped,
            }
        )
    else:
        push_sensor(
            'hepburn_finance_next_bill_amount',
            'unknown',
            {
                'friendly_name': 'Hepburn · Next bill',
                'unit_of_measurement': '$',
                'icon': 'mdi:receipt-text',
                'name': 'No upcoming bills',
                'days_away': None,
                'bills_list': [],
                'bills_grouped': [],
            }
        )

    # Debt and redraw totals — compute from hydrated balances (opening + tx sum)
    from app.balances import hydrate_accounts
    with get_db() as conn:
        loan_rows = conn.execute(
            "SELECT * FROM accounts WHERE archived=0 AND type IN "
            "('loan_investment','loan_personal','loan_informal','ppor','loan')"
        ).fetchall()
        redraw_total = conn.execute(
            "SELECT COALESCE(SUM(available_redraw), 0) FROM accounts "
            "WHERE archived=0 AND type IN "
            "('loan_investment','loan_personal','ppor','loan')"
        ).fetchone()[0]

    debt_accounts = hydrate_accounts(loan_rows)
    debt_total = sum(a['computed_balance'] for a in debt_accounts)

    push_sensor(
        'hepburn_finance_debt_total',
        round(debt_total, 2),
        {
            'friendly_name': 'Hepburn · Total debt',
            'unit_of_measurement': '$',
            'icon': 'mdi:scale-balance',
        }
    )
    push_sensor(
        'hepburn_finance_redraw_total',
        round(redraw_total, 2),
        {
            'friendly_name': 'Hepburn · Mortgage redraw available',
            'unit_of_measurement': '$',
            'icon': 'mdi:water-outline',
        }
    )

    # Spending budgets summary — for the HA popup
    from app.budgets import budget_status
    with get_db() as conn:
        budget_rows = conn.execute(
            "SELECT b.*, a.name AS account_name "
            "FROM spending_budgets b "
            "LEFT JOIN accounts a ON b.account_id = a.id "
            "WHERE b.active = 1 ORDER BY b.cadence, b.name"
        ).fetchall()
    budgets_data = []
    total_remaining = 0.0
    total_amount = 0.0
    total_spent = 0.0
    for b in budget_rows:
        s = budget_status(dict(b), today=today)
        budgets_data.append({
            'id': s['budget_id'],
            'name': s['name'],
            'category': s['category'],
            'cadence': s['cadence'],
            'amount': s['amount'],
            'spent': s['spent'],
            'remaining': s['remaining'],
            'pct_used': s['pct_used'],
            'days_remaining': s['days_remaining'],
            'over_budget': s['over_budget'],
        })
        total_amount += s['amount']
        total_spent += s['spent']
        total_remaining += s['remaining']

    push_sensor(
        'hepburn_finance_budgets_remaining',
        round(total_remaining, 2),
        {
            'friendly_name': 'Hepburn · Budget remaining (current period)',
            'unit_of_measurement': '$',
            'icon': 'mdi:wallet-outline',
            'count': len(budgets_data),
            'total_amount': round(total_amount, 2),
            'total_spent': round(total_spent, 2),
            'budgets': budgets_data,
        }
    )

    logger.info('HA sensors refreshed')
    return True


def start_periodic_push(interval_seconds=300):
    """Start a background thread that refreshes sensors periodically.

    Default 5 minutes. The thread is daemonised so it won't prevent shutdown.
    """
    if not SUPERVISOR_TOKEN:
        logger.info('No SUPERVISOR_TOKEN — periodic HA push disabled')
        return None

    def _loop():
        # First push slightly delayed so app finishes booting
        import time
        time.sleep(15)
        while True:
            try:
                compute_and_push_all()
            except Exception as e:
                logger.exception('HA push iteration failed: %s', e)
            time.sleep(interval_seconds)

    t = threading.Thread(target=_loop, daemon=True, name='ha-sensor-push')
    t.start()
    logger.info('HA sensor push thread started (interval=%ds)', interval_seconds)
    return t
