"""Generic CSV parser - tries to auto-detect Date, Description, Amount columns.

Handles common Aussie bank formats (CommBank, NAB, ANZ, Bankwest, Westpac).
Used as fallback when no specific parser matches.
"""
import csv
import hashlib
import re
from datetime import datetime
from io import StringIO

DATE_FORMATS = [
    '%d/%m/%Y', '%d/%m/%y',
    '%Y-%m-%d', '%d-%m-%Y',
    '%d %b %Y', '%d %B %Y',
    '%m/%d/%Y',
]

DATE_PATTERNS = [
    re.compile(r'^\d{1,2}[/-]\d{1,2}[/-]\d{2,4}$'),
    re.compile(r'^\d{4}-\d{1,2}-\d{1,2}$'),
]

AMOUNT_HEADERS = {'amount', 'value', 'debit', 'credit', 'transaction amount'}
DATE_HEADERS = {'date', 'transaction date', 'posted date', 'effective date'}
DESC_HEADERS = {'description', 'narration', 'details', 'transaction description', 'memo', 'particulars'}


def _try_parse_date(s):
    s = s.strip()
    for f in DATE_FORMATS:
        try:
            return datetime.strptime(s, f)
        except ValueError:
            continue
    return None


def _looks_like_date(s):
    s = s.strip()
    if any(p.match(s) for p in DATE_PATTERNS):
        return _try_parse_date(s) is not None
    return False


def _looks_like_amount(s):
    s = s.strip().replace(',', '').replace('$', '')
    try:
        float(s)
        return True
    except ValueError:
        return False


def parse(content_str, account_id):
    """Parse a generic bank CSV. Returns list of transaction dicts."""
    txs = []
    reader = csv.reader(StringIO(content_str))
    rows = list(reader)
    if not rows:
        return txs

    # Try to detect if first row is a header
    first = rows[0]
    has_header = False
    for cell in first:
        cell_low = cell.strip().lower()
        if cell_low in DATE_HEADERS or cell_low in AMOUNT_HEADERS or cell_low in DESC_HEADERS:
            has_header = True
            break

    if has_header:
        headers = [c.strip().lower() for c in first]
        data_rows = rows[1:]
        date_idx = next((i for i, h in enumerate(headers) if h in DATE_HEADERS), None)
        desc_idx = next((i for i, h in enumerate(headers) if h in DESC_HEADERS), None)
        # Could have separate Debit and Credit columns
        debit_idx = next((i for i, h in enumerate(headers) if h == 'debit'), None)
        credit_idx = next((i for i, h in enumerate(headers) if h == 'credit'), None)
        amt_idx = next((i for i, h in enumerate(headers) if h in AMOUNT_HEADERS and h not in ('debit','credit')), None)
    else:
        data_rows = rows
        date_idx = desc_idx = amt_idx = debit_idx = credit_idx = None

    # If no headers, autodetect by content
    for row in data_rows:
        if not row or len(row) < 3:
            continue

        if date_idx is None:
            for i, cell in enumerate(row):
                if _looks_like_date(cell):
                    date_idx = i
                    break

        d = None
        if date_idx is not None and date_idx < len(row):
            d = _try_parse_date(row[date_idx])
        if not d:
            continue

        # Amount detection
        amount = None
        if amt_idx is not None and amt_idx < len(row) and _looks_like_amount(row[amt_idx]):
            amount = float(row[amt_idx].strip().replace(',', '').replace('$', ''))
        elif debit_idx is not None and credit_idx is not None:
            debit = row[debit_idx].strip().replace(',', '').replace('$', '') if debit_idx < len(row) else ''
            credit = row[credit_idx].strip().replace(',', '').replace('$', '') if credit_idx < len(row) else ''
            if debit and _looks_like_amount(debit):
                amount = -abs(float(debit))
            elif credit and _looks_like_amount(credit):
                amount = abs(float(credit))
        else:
            # Find a numeric column
            for i, cell in enumerate(row):
                if i != date_idx and _looks_like_amount(cell):
                    amount = float(cell.strip().replace(',', '').replace('$', ''))
                    break
        if amount is None:
            continue

        # Description: take longest non-numeric, non-date cell
        if desc_idx is not None and desc_idx < len(row):
            description = row[desc_idx].strip()
        else:
            candidates = []
            for i, cell in enumerate(row):
                if i == date_idx:
                    continue
                cell = cell.strip()
                if cell and not _looks_like_amount(cell) and not _looks_like_date(cell):
                    candidates.append(cell)
            description = max(candidates, key=len) if candidates else 'Unknown'

        fp_data = f"{account_id}|{d.strftime('%Y-%m-%d')}|{amount:.2f}|{description}"
        fingerprint = hashlib.sha256(fp_data.encode()).hexdigest()[:32]

        txs.append({
            'account_id': account_id,
            'date': d.strftime('%Y-%m-%d'),
            'amount': amount,
            'description': description,
            'raw_description': description,
            'transaction_type': '',
            'reference': '',
            'fingerprint': fingerprint,
        })

    return txs


def detect(content_str):
    """Always returns True - generic is the fallback."""
    return True
