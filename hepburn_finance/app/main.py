"""Flask application entry point. Started by /etc/services.d/finance/run via Docker."""
import os
import logging
from flask import Flask
from app.database import init_db
from app.routes import bp


class IngressMiddleware:
    """WSGI middleware to make Flask aware of the HA Ingress path prefix.

    HA Ingress sends an `X-Ingress-Path` header on every request — that's the
    base path the user hits (e.g. /api/hassio_ingress/abc123). For url_for()
    to generate working links, we need to set SCRIPT_NAME so Flask thinks
    the app is mounted at that prefix.
    """
    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        ingress = environ.get('HTTP_X_INGRESS_PATH', '')
        if ingress:
            environ['SCRIPT_NAME'] = ingress
            path_info = environ.get('PATH_INFO', '')
            if path_info.startswith(ingress):
                environ['PATH_INFO'] = path_info[len(ingress):]
        return self.app(environ, start_response)


def create_app():
    app = Flask(__name__, static_folder='static', template_folder='templates')
    app.secret_key = os.environ.get('FLASK_SECRET', 'hepburn-dev-secret-change-me')
    app.wsgi_app = IngressMiddleware(app.wsgi_app)
    app.register_blueprint(bp)
    return app


def seed_initial_accounts():
    """If the accounts table is empty, seed with the Bendigo + Latitude
    accounts we know about. User can edit/delete via the UI."""
    from app.database import get_db
    with get_db() as conn:
        count = conn.execute('SELECT COUNT(*) FROM accounts').fetchone()[0]
        if count > 0:
            return

        bendigo = [
            ('Bendigo', 'Card Account',           'Day-to-day debit',     '223 214 727', 'transaction', 87.06,    20.12, None, None, 0),
            ('Bendigo', 'Income & Bills Account', 'Main hub · salary in', '223 214 818', 'transaction', 160.20,   None,  None, None, 0),
            ('Bendigo', 'Rainy Day Funds',        'Sub-account',          '223 214 826', 'savings',     0.00,     None,  None, None, 0),
            ('Bendigo', 'Other Peoples Money',    'Sub-account',          '223 214 842', 'savings',     0.00,     None,  None, None, 0),
            ('Bendigo', 'Holiday Funds',          'Sub-account',          '223 214 859', 'savings',     0.00,     None,  None, None, 0),
            ('Bendigo', 'Tax Account',            'Sub-account',          '223 214 867', 'savings',     0.00,     None,  None, None, 0),
            ('Bendigo', 'Mortgage Loan (PPOR)',   'P&I home loan',        '703 950 915', 'ppor',        -545429.61, None, None, 6.0,  0),
            ('Bendigo', 'Robina Mortgage',        'Investment property',  '703 952 259', 'loan',        -363533.74, None, None, 6.0,  1),
            ('Bendigo', 'Nundah Mortgage',        'Investment property',  '703 952 309', 'loan',        -405400.00, None, None, 6.0,  1),
        ]
        latitude = [
            ('Latitude', 'Gem Visa', '6010 ···· 8259', '6010 7320 0426 8259', 'credit', -14549.64, 450.36, 15000, 28.49, 0),
        ]
        for a in bendigo + latitude:
            conn.execute(
                'INSERT INTO accounts (bank, name, nickname, account_number, type, '
                'balance, available, credit_limit, interest_rate, is_deductible) '
                'VALUES (?,?,?,?,?,?,?,?,?,?)', a
            )

        gem_visa_id = conn.execute("SELECT id FROM accounts WHERE name='Gem Visa'").fetchone()['id']
        plans = [
            ('Penrith Auto',                'Started 20 Mar', 1529.87, 1529.87, None, '2026-10-18', 29.99),
            ('Pandora purchase',            'Started 28 Oct', 559.17,  559.17,  None, '2026-05-18', 29.99),
            ('December purchase',           'Started 5 Jan',  270.00,  270.00,  None, '2026-07-18', 29.99),
            ('February purchase',           'Started 1 Mar',  648.45,  648.45,  None, '2026-09-18', 29.99),
            ('Harvey Norman (electrical)',  '33 mths min',    2601.00, 2586.00, None, '2026-09-26', 29.99),
            ('Harvey Norman (computer)',    '$86.12/mo',      3100.00, 1291.48, 86.12, '2027-07-14', 29.99),
            ('Amazon (small)',              '$5.83/mo',       69.99,   17.52,   5.83, '2026-06-29', 29.99),
            ('Amazon (larger)',             '$43.43/mo',      521.12,  130.25,  43.43, '2026-07-01', 29.99),
        ]
        for p in plans:
            conn.execute(
                'INSERT INTO interest_free_plans (account_id, name, detail, starting_balance, '
                'current_balance, monthly_payment, expiry_date, expired_rate) '
                'VALUES (?,?,?,?,?,?,?,?)',
                (gem_visa_id,) + p
            )


def main():
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
    init_db()
    seed_initial_accounts()
    app = create_app()
    port = int(os.environ.get('FINANCE_PORT', 8765))
    logging.info(f'Starting Hepburn Finance on port {port}')
    app.run(host='0.0.0.0', port=port, debug=False)


if __name__ == '__main__':
    main()
