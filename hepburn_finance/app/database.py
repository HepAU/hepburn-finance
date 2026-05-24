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
    type TEXT NOT NULL,
    balance REAL NOT NULL DEFAULT 0,
    opening_balance REAL,
    balance_last_updated TEXT,
    available REAL,
    available_redraw REAL,
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
    end_date TEXT,
    occurrences_remaining INTEGER,
    category TEXT,
    account_id INTEGER NOT NULL,
    is_income INTEGER DEFAULT 0,
    payee_pattern TEXT,
    active INTEGER DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (account_id) REFERENCES accounts(id)
);

CREATE TABLE IF NOT EXISTS scheduled_transfers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    amount REAL NOT NULL,
    next_date TEXT NOT NULL,
    recurring TEXT NOT NULL DEFAULT 'monthly'
        CHECK(recurring IN ('once','weekly','fortnightly','monthly','quarterly','yearly')),
    end_date TEXT,
    occurrences_remaining INTEGER,
    from_account_id INTEGER NOT NULL,
    to_account_id INTEGER NOT NULL,
    category TEXT,
    notes TEXT,
    active INTEGER DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (from_account_id) REFERENCES accounts(id),
    FOREIGN KEY (to_account_id) REFERENCES accounts(id),
    CHECK(from_account_id != to_account_id)
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

CREATE TABLE IF NOT EXISTS spending_budgets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    amount REAL NOT NULL,
    cadence TEXT NOT NULL,
    account_id INTEGER,
    notes TEXT,
    active INTEGER DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS afterpay_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store TEXT NOT NULL,
    total_amount REAL NOT NULL,
    purchase_date TEXT NOT NULL,
    instalment_count INTEGER NOT NULL DEFAULT 4,
    account_id INTEGER,
    archived INTEGER DEFAULT 0,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (account_id) REFERENCES accounts(id)
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


def _column_exists(conn, table, column):
    """Check if a column exists on a table."""
    cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(c[1] == column for c in cols)


def _table_exists(conn, table):
    """Check if a table exists."""
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def run_migrations(conn):
    """Apply schema changes for users upgrading from earlier versions.

    Each migration is idempotent — safe to run repeatedly.
    """
    # 0.1.3: available_redraw column on accounts
    if not _column_exists(conn, 'accounts', 'available_redraw'):
        conn.execute('ALTER TABLE accounts ADD COLUMN available_redraw REAL')

    # 0.1.3: end_date and occurrences_remaining on scheduled_bills
    if not _column_exists(conn, 'scheduled_bills', 'end_date'):
        conn.execute('ALTER TABLE scheduled_bills ADD COLUMN end_date TEXT')
    if not _column_exists(conn, 'scheduled_bills', 'occurrences_remaining'):
        conn.execute('ALTER TABLE scheduled_bills ADD COLUMN occurrences_remaining INTEGER')

    # 0.1.3: scheduled_transfers table
    if not _table_exists(conn, 'scheduled_transfers'):
        conn.execute("""
            CREATE TABLE scheduled_transfers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                amount REAL NOT NULL,
                next_date TEXT NOT NULL,
                recurring TEXT NOT NULL DEFAULT 'monthly'
                    CHECK(recurring IN ('once','weekly','fortnightly','monthly','quarterly','yearly')),
                end_date TEXT,
                occurrences_remaining INTEGER,
                from_account_id INTEGER NOT NULL,
                to_account_id INTEGER NOT NULL,
                category TEXT,
                notes TEXT,
                active INTEGER DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (from_account_id) REFERENCES accounts(id),
                FOREIGN KEY (to_account_id) REFERENCES accounts(id),
                CHECK(from_account_id != to_account_id)
            )
        """)

    # 0.2.0: rebuild accounts table to drop the strict type CHECK constraint
    # and migrate `loan` -> `loan_investment` to make room for new types:
    # loan_personal (formal personal/solar/car loans) and loan_informal
    # (borrowed from family/friends/work).
    #
    # SQLite needs a table-rebuild to change a CHECK constraint, so we
    # detect the old constraint by trying to insert an unknown type and
    # checking for failure.
    needs_rebuild = False
    try:
        # If this insert succeeds, the old CHECK is gone. Roll back regardless.
        conn.execute("SAVEPOINT type_check_test")
        conn.execute(
            "INSERT INTO accounts (bank, name, type) VALUES (?, ?, ?)",
            ('__migration_test__', '__migration_test__', 'loan_personal')
        )
        conn.execute("ROLLBACK TO type_check_test")
        conn.execute("RELEASE type_check_test")
    except Exception:
        # Old CHECK still in place
        try:
            conn.execute("ROLLBACK TO type_check_test")
            conn.execute("RELEASE type_check_test")
        except Exception:
            pass
        needs_rebuild = True

    if needs_rebuild:
        # Move old data, drop, recreate, copy back, drop temp.
        # Foreign key constraints from transactions/scheduled_bills/scheduled_transfers
        # reference accounts(id), so we keep ids stable.
        conn.execute('PRAGMA foreign_keys = OFF')
        conn.execute("""
            CREATE TABLE accounts_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bank TEXT NOT NULL,
                name TEXT NOT NULL,
                nickname TEXT,
                account_number TEXT,
                type TEXT NOT NULL,
                balance REAL NOT NULL DEFAULT 0,
                available REAL,
                available_redraw REAL,
                credit_limit REAL,
                interest_rate REAL,
                is_deductible INTEGER DEFAULT 0,
                include_in_forecast INTEGER DEFAULT 1,
                selected_default INTEGER DEFAULT 0,
                archived INTEGER DEFAULT 0,
                notes TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        # Copy with type translation: loan -> loan_investment
        # COALESCE on timestamps in case old rows have NULLs
        conn.execute("""
            INSERT INTO accounts_new
            SELECT id, bank, name, nickname, account_number,
                   CASE WHEN type='loan' THEN 'loan_investment' ELSE type END,
                   COALESCE(balance, 0),
                   available, available_redraw, credit_limit,
                   interest_rate,
                   COALESCE(is_deductible, 0),
                   COALESCE(include_in_forecast, 1),
                   COALESCE(selected_default, 0),
                   COALESCE(archived, 0),
                   notes,
                   COALESCE(created_at, datetime('now')),
                   COALESCE(updated_at, datetime('now'))
            FROM accounts
        """)
        conn.execute('DROP TABLE accounts')
        conn.execute('ALTER TABLE accounts_new RENAME TO accounts')
        conn.execute('PRAGMA foreign_keys = ON')

    # 0.2.0: transaction edit support — add updated_at + notes columns,
    # and is_internal_transfer flag for auto-detection
    if not _column_exists(conn, 'transactions', 'updated_at'):
        conn.execute('ALTER TABLE transactions ADD COLUMN updated_at TEXT')
    if not _column_exists(conn, 'transactions', 'notes'):
        conn.execute('ALTER TABLE transactions ADD COLUMN notes TEXT')
    if not _column_exists(conn, 'transactions', 'is_internal_transfer'):
        conn.execute('ALTER TABLE transactions ADD COLUMN is_internal_transfer INTEGER DEFAULT 0')
    if not _column_exists(conn, 'transactions', 'transfer_pair_id'):
        # Links the two legs of a detected internal transfer to each other.
        conn.execute('ALTER TABLE transactions ADD COLUMN transfer_pair_id INTEGER')

    # 0.2.1: clean up literal 'None' strings stored in text fields by old
    # account form template (Jinja was rendering Python None as the literal
    # string "None" which then got saved on form re-submission).
    for table, cols in (
        ('accounts', ['account_number', 'nickname', 'notes']),
        ('scheduled_bills', ['category']),
        ('scheduled_transfers', ['category', 'notes']),
        ('transactions', ['notes']),
    ):
        if _table_exists(conn, table):
            for col in cols:
                if _column_exists(conn, table, col):
                    conn.execute(
                        f"UPDATE {table} SET {col}=NULL WHERE {col}='None'"
                    )

    # 0.4.0: balance-lock model. Add opening_balance and balance_last_updated.
    # When opening_balance is non-null, the displayed balance is computed from
    # opening_balance + sum(ALL transactions, including internal transfers).
    # See app/balances.py:compute_account_balance for the canonical formula —
    # opening_balance already reflects every bank movement (transfers included),
    # so excluding internal transfers from the sum would double-correct them.
    # The legacy `balance` column stays as a fallback for accounts that never
    # get a real opening value set (manual mode).
    #
    # Migration strategy: copy current `balance` into `opening_balance` for
    # every existing account and set balance_last_updated to now. From this
    # point on, balance computation uses opening_balance + transactions.
    if not _column_exists(conn, 'accounts', 'opening_balance'):
        conn.execute('ALTER TABLE accounts ADD COLUMN opening_balance REAL')
        # Seed opening_balance from current balance for existing accounts
        conn.execute(
            'UPDATE accounts SET opening_balance = balance '
            'WHERE opening_balance IS NULL'
        )
    if not _column_exists(conn, 'accounts', 'balance_last_updated'):
        conn.execute(
            "ALTER TABLE accounts ADD COLUMN balance_last_updated TEXT"
        )
        conn.execute(
            "UPDATE accounts SET balance_last_updated = COALESCE(updated_at, datetime('now'))"
        )

    # 0.6.0: spending_budgets table — weekly/fortnightly/monthly budgets per
    # category with optional account targeting.
    if not _table_exists(conn, 'spending_budgets'):
        conn.execute("""
            CREATE TABLE spending_budgets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                category TEXT NOT NULL,
                amount REAL NOT NULL,
                cadence TEXT NOT NULL,
                account_id INTEGER,
                notes TEXT,
                active INTEGER DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)

    # 0.7.0: afterpay_orders table — first-class grouping of Afterpay
    # instalments so the user can see all active orders at a glance with
    # paid/unpaid pips per payment. Existing bills named "Afterpay · …" are
    # backfilled into orders below.
    if not _table_exists(conn, 'afterpay_orders'):
        conn.execute("""
            CREATE TABLE afterpay_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                store TEXT NOT NULL,
                total_amount REAL NOT NULL,
                purchase_date TEXT NOT NULL,
                instalment_count INTEGER NOT NULL DEFAULT 4,
                account_id INTEGER,
                archived INTEGER DEFAULT 0,
                notes TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (account_id) REFERENCES accounts(id)
            )
        """)
    # Link columns on scheduled_bills connect each instalment back to its
    # parent order + records its position in the sequence and whether the
    # user has marked it paid.
    if not _column_exists(conn, 'scheduled_bills', 'afterpay_order_id'):
        conn.execute('ALTER TABLE scheduled_bills ADD COLUMN afterpay_order_id INTEGER')
    if not _column_exists(conn, 'scheduled_bills', 'afterpay_seq'):
        conn.execute('ALTER TABLE scheduled_bills ADD COLUMN afterpay_seq INTEGER')
    if not _column_exists(conn, 'scheduled_bills', 'afterpay_paid'):
        conn.execute('ALTER TABLE scheduled_bills ADD COLUMN afterpay_paid INTEGER DEFAULT 0')
    if not _column_exists(conn, 'scheduled_bills', 'afterpay_paid_at'):
        conn.execute('ALTER TABLE scheduled_bills ADD COLUMN afterpay_paid_at TEXT')

    # Backfill: parse pre-0.7.0 bills with the legacy
    # "Afterpay · {store} ({seq} of {n})" name shape into proper orders.
    # Idempotent — only touches bills that aren't already linked.
    import re as _re
    backfill_rows = conn.execute(
        "SELECT id, name, amount, next_date, account_id FROM scheduled_bills "
        "WHERE afterpay_order_id IS NULL AND name LIKE 'Afterpay · %'"
    ).fetchall()
    groups = {}
    for r in backfill_rows:
        m = _re.match(r'^Afterpay · (.+?) \((\d+) of (\d+)\)$', r['name'])
        if not m:
            continue
        store = m.group(1)
        seq = int(m.group(2))
        count = int(m.group(3))
        key = (store, count, r['account_id'])
        groups.setdefault(key, []).append({
            'id': r['id'], 'seq': seq, 'amount': r['amount'], 'next_date': r['next_date']
        })
    today_iso = datetime.now().date().isoformat()
    for (store, count, account_id), items in groups.items():
        items.sort(key=lambda x: x['seq'])
        purchase_date = items[0]['next_date']
        total = round(sum(abs(x['amount']) for x in items), 2)
        cur = conn.execute(
            "INSERT INTO afterpay_orders (store, total_amount, purchase_date, "
            "instalment_count, account_id) VALUES (?,?,?,?,?)",
            (store, total, purchase_date, count, account_id)
        )
        new_oid = cur.lastrowid
        for it in items:
            # Mark already-past instalments as paid — best guess; the user
            # has presumably been paying them. They can toggle if wrong.
            already_paid = 1 if it['next_date'] < today_iso else 0
            paid_at = it['next_date'] if already_paid else None
            conn.execute(
                "UPDATE scheduled_bills SET afterpay_order_id=?, afterpay_seq=?, "
                "afterpay_paid=?, afterpay_paid_at=? WHERE id=?",
                (new_oid, it['seq'], already_paid, paid_at, it['id'])
            )


def init_db():
    """Create tables and seed default rules on first run."""
    os.makedirs(DATA_DIR, exist_ok=True)
    with get_db() as conn:
        conn.executescript(SCHEMA)
        run_migrations(conn)

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
