"""Database setup and schema for Hepburn Finance.

Single SQLite file stored in /data (persistent HA add-on volume).
"""
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime

DATA_DIR = os.environ.get('FINANCE_DATA_DIR', '/data')
DB_PATH = os.path.join(DATA_DIR, 'finance.db')

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bank TEXT NOT NULL,
    name TEXT NOT NULL,
    nickname TEXT,
    account_number TEXT,
    type TEXT NOT NULL CHECK(type IN ('transaction','savings','credit','loan','ppor')),
    balance REAL NOT NULL DEFAULT 0,
    available REAL,
    credit_limit REAL,
    interest_rate REAL,
    is_deductible INTEGER DEFAULT 0,
    include_in_forecast INTEGER DEFAULT 1,
    selected_default INTEGER DEFAULT 0,
    archived INTEGER DEFAULT 0,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,
    date TEXT NOT NULL,
    amount REAL NOT NULL,
    description TEXT NOT NULL,
    raw_description TEXT,
    transaction_type TEXT,
    reference TEXT,
    category TEXT,
    matched_bill_id INTEGER,
    user_categorised INTEGER DEFAULT 0,
    fingerprint TEXT UNIQUE,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (account_id) REFERENCES accounts(id),
    FOREIGN KEY (matched_bill_id) REFERENCES scheduled_bills(id)
);

CREATE INDEX IF NOT EXISTS idx_tx_account_date ON transactions(account_id, date DESC);
CREATE INDEX IF NOT EXISTS idx_tx_date ON transactions(date DESC);

CREATE TABLE IF NOT EXISTS scheduled_bills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    amount REAL NOT NULL,
    next_date TEXT NOT NULL,
    recurring TEXT NOT NULL DEFAULT 'monthly'
        CHECK(recurring IN ('once','weekly','fortnightly','monthly','quarterly','yearly')),
    category TEXT,
    account_id INTEGER NOT NULL,
    is_income INTEGER DEFAULT 0,
    payee_pattern TEXT,
    active INTEGER DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (account_id) REFERENCES accounts(id)
);

CREATE TABLE IF NOT EXISTS interest_free_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    detail TEXT,
    starting_balance REAL NOT NULL,
    current_balance REAL NOT NULL,
    monthly_payment REAL,
    expiry_date TEXT NOT NULL,
    expired_rate REAL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (account_id) REFERENCES accounts(id)
);

CREATE TABLE IF NOT EXISTS category_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern TEXT NOT NULL,
    pattern_type TEXT NOT NULL DEFAULT 'contains'
        CHECK(pattern_type IN ('contains','starts_with','exact','regex')),
    category TEXT NOT NULL,
    priority INTEGER DEFAULT 0,
    user_added INTEGER DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS notifications_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sent_at TEXT NOT NULL DEFAULT (datetime('now')),
    kind TEXT NOT NULL,
    title TEXT,
    body TEXT,
    target TEXT
);
"""

# Default category rules — seeded on first run, editable via UI
DEFAULT_RULES = [
    ('WOOLWORTHS', 'contains', 'Groceries', 100),
    ('COLES', 'contains', 'Groceries', 100),
    ('ALDI', 'contains', 'Groceries', 100),
    ('BUNNINGS', 'contains', 'Home & Garden', 100),
    ('IKEA', 'contains', 'Home & Garden', 100),
    ('BEACON LIGHTING', 'contains', 'Home & Garden', 100),
    ('BIGW', 'contains', 'Household', 100),
    ('BIG W', 'contains', 'Household', 100),
    ('KMART', 'contains', 'Household', 100),
    ('TARGET', 'contains', 'Household', 100),
    ('MCDONALD', 'contains', 'Eating Out', 100),
    ('SUBWAY', 'contains', 'Eating Out', 100),
    ('KEBAB', 'contains', 'Eating Out', 100),
    ('SUSHI', 'contains', 'Eating Out', 100),
    ('UBER EATS', 'contains', 'Eating Out', 110),
    ('MENULOG', 'contains', 'Eating Out', 110),
    ('DOORDASH', 'contains', 'Eating Out', 110),
    ('AMPOL', 'contains', 'Fuel', 100),
    ('CALTEX', 'contains', 'Fuel', 100),
    ('SHELL', 'contains', 'Fuel', 90),
    ('BP ', 'contains', 'Fuel', 90),
    ('7-ELEVEN', 'contains', 'Fuel', 90),
    ('UBER TRIP', 'contains', 'Transport', 100),
    ('UBER ', 'contains', 'Transport', 90),
    ('OPAL', 'contains', 'Transport', 100),
    ('AFTERPAY', 'contains', 'Afterpay', 110),
    ('AMZNPRIME', 'contains', 'Subscriptions', 100),
    ('NETFLIX', 'contains', 'Subscriptions', 100),
    ('SPOTIFY', 'contains', 'Subscriptions', 100),
    ('YOUTUBE', 'contains', 'Subscriptions', 100),
    ('PRIME VIDE', 'contains', 'Subscriptions', 100),
    ('DISNEY', 'contains', 'Subscriptions', 100),
    ('NRMA', 'contains', 'Insurance · Car', 100),
    ('NIB', 'contains', 'Insurance · Health', 100),
    ('BUPA', 'contains', 'Insurance · Health', 100),
    ('MEDIBANK', 'contains', 'Insurance · Health', 100),
    ('AAMI', 'contains', 'Insurance', 100),
    ('ALLIANZ', 'contains', 'Insurance', 100),
    ('RED ENERGY', 'contains', 'Utilities · Electricity', 100),
    ('ORIGIN ENERGY', 'contains', 'Utilities · Electricity', 100),
    ('AGL', 'contains', 'Utilities · Electricity', 100),
    ('ENERGYAUSTRALIA', 'contains', 'Utilities · Electricity', 100),
    ('VODAFONE', 'contains', 'Utilities · Phone', 100),
    ('TELSTRA', 'contains', 'Utilities · Phone', 100),
    ('OPTUS', 'contains', 'Utilities · Phone', 100),
    ('AUSSIE BROADBAND', 'contains', 'Utilities · Internet', 100),
    ('TPG', 'contains', 'Utilities · Internet', 100),
    ('SYDNEY WATER', 'contains', 'Utilities · Water', 100),
    ('URBAN UTILITIES', 'contains', 'Utilities · Water', 100),
    ('PENRITH CITY COUNCIL', 'contains', 'Council Rates', 100),
    ('BCC RATES', 'contains', 'Council Rates', 100),
    ('GCCC', 'contains', 'Council Rates', 100),
    ('STRATAPAY', 'contains', 'Strata', 100),
    ('STRATA', 'contains', 'Strata', 90),
    ('SALARY', 'contains', 'Income · Salary', 110),
    ('PAYROLL', 'contains', 'Income · Salary', 110),
    ('REGO', 'contains', 'Car · Rego', 100),
    ('TWINKL', 'contains', 'Education', 100),
    ('POCKET MONEY', 'contains', 'Kids · Pocket Money', 100),
    ('JORDANS TEETH', 'contains', 'Kids · Dental', 100),
    ('INTEREST', 'exact', 'Loan · Interest', 110),
    ('MONTHLY SERVICE FEE', 'exact', 'Bank Fees', 110),
    ('DEFAULT FEE', 'exact', 'Bank Fees', 110),
    ('INTERNATIONAL TRANSACTION FEE', 'exact', 'Bank Fees · FX', 110),
]


@contextmanager
def get_db():
    """Yield a SQLite connection with foreign keys enabled and Row factory set."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Create tables and seed default rules on first run."""
    os.makedirs(DATA_DIR, exist_ok=True)
    with get_db() as conn:
        conn.executescript(SCHEMA)

        # Seed category rules if table is empty
        count = conn.execute('SELECT COUNT(*) FROM category_rules').fetchone()[0]
        if count == 0:
            conn.executemany(
                'INSERT INTO category_rules (pattern, pattern_type, category, priority) VALUES (?,?,?,?)',
                DEFAULT_RULES
            )

        # First-run seed: add a stub setting so we can detect later
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('initialised_at', ?)",
            (datetime.now().isoformat(),)
        )


def get_setting(key, default=None):
    with get_db() as conn:
        row = conn.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return row['value'] if row else default


def set_setting(key, value):
    with get_db() as conn:
        conn.execute(
            'INSERT INTO settings (key, value) VALUES (?, ?) '
            'ON CONFLICT(key) DO UPDATE SET value = excluded.value',
            (key, str(value))
        )
