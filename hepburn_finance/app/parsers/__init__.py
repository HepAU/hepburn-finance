"""Parser registry. Auto-detects format and dispatches to the right parser."""
from app.parsers import bendigo, generic


PARSERS = [bendigo, generic]


def parse_csv(content_str, account_id):
    """Try parsers in order, returning the first non-empty result."""
    for parser in PARSERS:
        if parser.detect(content_str):
            txs = parser.parse(content_str, account_id)
            if txs:
                return txs, parser.__name__.split('.')[-1]
    return [], 'none'
