"""Transaction categorisation.

Default: rule-based (uses category_rules table).
Optional: AI-powered (Claude/Gemini) for transactions that don't match rules.
"""
import os
import re
from app.database import get_db


# Tokens that appear at the start of bank descriptions but say nothing about
# the merchant. Used by merchant_token() — if the first word of a description
# is one of these, we skip past it to find a meaningful identifier.
_GENERIC_DESCRIPTION_TOKENS = frozenset({
    'DIRECT', 'DEBIT', 'CREDIT', 'EFTPOS', 'POS', 'VISA', 'MASTERCARD',
    'PURCHASE', 'PAYMENT', 'TRANSFER', 'WITHDRAWAL', 'DEPOSIT', 'REFUND',
    'INTERNET', 'BPAY', 'OSKO', 'PAYID', 'NPP', 'ATM', 'CHQ', 'CHEQUE',
    'FROM', 'TO', 'AT',
})


def merchant_token(description):
    """Return a stable merchant-identifying token from a transaction description.

    Strips banking prefixes like 'DIRECT DEBIT' / 'EFTPOS PURCHASE' / 'VISA'
    and returns the first remaining alphabetic token of length >= 3, upper-cased.
    Returns None if no usable token is found.

    Used for bulk-tagging similar transactions and (in future) for auto-creating
    category rules from user edits.
    """
    if not description:
        return None
    for tok in description.upper().split():
        # Strip leading/trailing non-alpha (e.g. "WOOLWORTHS:" → "WOOLWORTHS",
        # "*MERCHANT" → "MERCHANT") but bail on internal mixes like "PYM123".
        stripped = tok.strip('.,:;*#-/()[]"\'')
        if not stripped.isalpha():
            continue
        if len(stripped) < 3:
            continue
        if stripped in _GENERIC_DESCRIPTION_TOKENS:
            continue
        return stripped
    return None


def categorise_by_rules(description, transaction_type=''):
    """Apply category rules from the database. Returns category string or None."""
    desc_upper = description.upper()
    type_upper = (transaction_type or '').upper()

    with get_db() as conn:
        rules = conn.execute(
            'SELECT pattern, pattern_type, category FROM category_rules ORDER BY priority DESC, id ASC'
        ).fetchall()

    for r in rules:
        pattern = r['pattern']
        ptype = r['pattern_type']

        # Rules can match either description or transaction_type.
        # Check both.
        for haystack in (desc_upper, type_upper):
            if not haystack:
                continue
            if ptype == 'contains' and pattern in haystack:
                return r['category']
            if ptype == 'starts_with' and haystack.startswith(pattern):
                return r['category']
            if ptype == 'exact' and haystack == pattern:
                return r['category']
            if ptype == 'regex':
                try:
                    if re.search(pattern, haystack):
                        return r['category']
                except re.error:
                    continue

    return None


def categorise_batch(transactions, ai_provider='none', ai_api_key=''):
    """Categorise a list of transactions in-place.

    Each transaction dict is updated with a 'category' key.
    AI provider is used only for transactions that don't match rules.
    """
    uncategorised = []
    for tx in transactions:
        cat = categorise_by_rules(tx['description'], tx.get('transaction_type', ''))
        if cat:
            tx['category'] = cat
        else:
            tx['category'] = 'Uncategorised'
            uncategorised.append(tx)

    # If no AI, leave as Uncategorised
    if ai_provider == 'none' or not ai_api_key or not uncategorised:
        return transactions

    # AI batch - for now we only call AI if explicitly enabled
    if ai_provider == 'claude':
        _categorise_with_claude(uncategorised, ai_api_key)
    elif ai_provider == 'gemini':
        _categorise_with_gemini(uncategorised, ai_api_key)

    return transactions


def _categorise_with_claude(transactions, api_key):
    """Stub for Claude API categorisation. Implemented later when API key available."""
    # Intentionally no-op for v0.1 — keeps the code path open without making
    # network calls until the user configures an API key. When implemented,
    # this batches up to ~30 transactions per call into a single message and
    # parses the JSON response back into the tx dicts.
    pass


def _categorise_with_gemini(transactions, api_key):
    """Stub for Gemini API categorisation."""
    pass


def add_user_rule(pattern, category, pattern_type='contains', conn=None):
    """Upsert a user-defined rule. Higher priority than defaults.

    Pass `conn` to participate in an existing transaction (e.g. when called
    from inside an edit_transaction handler that already holds the write
    lock). When `conn` is None, opens its own short-lived connection.

    Returns a tuple (action, previous_category):
      ('created', None)       — new rule inserted
      ('updated', old_cat)    — existing user rule had a different category; updated
      ('unchanged', category) — existing user rule already maps to this category
      ('skipped', None)       — pattern is empty/None; no-op
    """
    if not pattern:
        return ('skipped', None)
    pat = pattern.upper()

    def _do(c):
        existing = c.execute(
            'SELECT id, category FROM category_rules '
            'WHERE pattern=? AND pattern_type=? AND user_added=1',
            (pat, pattern_type)
        ).fetchone()
        if existing is None:
            c.execute(
                'INSERT INTO category_rules (pattern, pattern_type, category, priority, user_added) '
                'VALUES (?, ?, ?, 200, 1)',
                (pat, pattern_type, category)
            )
            return ('created', None)
        if existing['category'] == category:
            return ('unchanged', category)
        c.execute(
            'UPDATE category_rules SET category=? WHERE id=?',
            (category, existing['id'])
        )
        return ('updated', existing['category'])

    if conn is not None:
        return _do(conn)
    with get_db() as new_conn:
        return _do(new_conn)
