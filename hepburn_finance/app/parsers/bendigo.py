"""Parser for Bendigo Bank CSV exports.

Format observed in Hepburn data:
"633-000 223214818","27/04/2026","JDR10","-143.72","DIRECT DEBIT","NRMA Insurance      0502981874","HOMN00020727990037"

Columns: account_number, date_dmy, code, amount, transaction_type, description, reference
"""
import csv
import hashlib
from datetime import datetime
from io import StringIO


def parse(content_str, account_id):
    """Parse Bendigo CSV content. Returns list of transaction dicts."""
    txs = []
    reader = csv.reader(StringIO(content_str))
    for row in reader:
        if len(row) < 6:
            continue
        try:
            account_number = row[0].strip()
            date_str = row[1].strip()
            code = row[2].strip()
            amount = float(row[3])
            tx_type = row[4].strip()
            description = row[5].strip() if len(row) > 5 else ''
            reference = row[6].strip() if len(row) > 6 else ''
        except (ValueError, IndexError):
            continue

        try:
            d = datetime.strptime(date_str, '%d/%m/%Y')
        except ValueError:
            continue

        # If description is empty, use the transaction type as the description
        # (e.g. INTEREST entries have no description in the CSV)
        if not description:
            description = tx_type

        # Fingerprint to deduplicate when re-uploading the same CSV.
        # Includes account, date, amount and full description so legitimate
        # repeating charges (e.g. monthly $10 fee) on the same day still record.
        fp_data = f"{account_number}|{d.strftime('%Y-%m-%d')}|{amount:.2f}|{description}|{reference}"
        fingerprint = hashlib.sha256(fp_data.encode()).hexdigest()[:32]

        txs.append({
            'account_id': account_id,
            'date': d.strftime('%Y-%m-%d'),
            'amount': amount,
            'description': description,
            'raw_description': description,
            'transaction_type': tx_type,
            'reference': reference,
            'fingerprint': fingerprint,
        })
    return txs


def detect(content_str):
    """Quick check if a file looks like a Bendigo export.
    Heuristic: lines have 6-7 quoted columns, second column is dd/mm/yyyy date,
    first column starts with a BSB-like '633-'."""
    sample = content_str[:2000].splitlines()[:5]
    if not sample:
        return False
    matches = 0
    for line in sample:
        try:
            row = next(csv.reader([line]))
            if (len(row) >= 6 and
                row[0].startswith('633-') and
                len(row[1]) == 10 and row[1][2] == '/' and row[1][5] == '/'):
                matches += 1
        except Exception:
            pass
    return matches >= max(1, len(sample) // 2)
