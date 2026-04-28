"""Transaction categorisation.

Default: rule-based (uses category_rules table).
Optional: AI-powered (Claude/Gemini) for transactions that don't match rules.
"""
import os
import re
from app.database import get_db


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


def add_user_rule(pattern, category, pattern_type='contains'):
    """Add a user-defined rule. Higher priority than defaults."""
    with get_db() as conn:
        conn.execute(
            'INSERT INTO category_rules (pattern, pattern_type, category, priority, user_added) '
            'VALUES (?, ?, ?, 200, 1)',
            (pattern.upper(), pattern_type, category)
        )
