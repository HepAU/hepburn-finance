"""Smart suggestions engine for Hepburn Finance.

Each suggestion module returns a list of suggestion dicts with this shape:

    {
        'icon': str (emoji or single character),
        'priority': 'urgent' | 'attention' | 'good',
        'text': str (HTML-safe headline),
        'reasoning': str (italic explanation underneath),
        'action': str (button label),
        'kind': str (short identifier — used for analytics / dedup),
    }

The order returned by `smart_suggestions` is the order they appear in the UI.
Suggestions sort by priority bucket (urgent → attention → good) but otherwise
preserve the order their producing module returns them in.
"""
from datetime import date

from app.suggestions.cashflow import cashflow_suggestions, bill_clustering_suggestions
from app.suggestions.plans import interest_free_plan_suggestions
from app.suggestions.subscriptions import subscription_audit_suggestions
from app.suggestions.trends import category_trend_suggestions
from app.suggestions.drift import discretionary_drift_suggestions


PRIORITY_ORDER = {'urgent': 0, 'attention': 1, 'good': 2}


def smart_suggestions(account_ids, today=None):
    """Combine all suggestion modules and return a sorted list."""
    if today is None:
        today = date.today()

    out = []
    out.extend(cashflow_suggestions(account_ids, today))
    out.extend(bill_clustering_suggestions(account_ids, today))
    out.extend(interest_free_plan_suggestions(today))
    out.extend(subscription_audit_suggestions(today))
    out.extend(category_trend_suggestions(today))
    out.extend(discretionary_drift_suggestions(today))

    # Stable sort by priority bucket — keeps within-bucket order
    out.sort(key=lambda s: PRIORITY_ORDER.get(s.get('priority', 'good'), 99))

    return out
