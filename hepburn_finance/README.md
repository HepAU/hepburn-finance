# Hepburn Finance

A family finance dashboard with cash flow forecasting, scheduled bill management,
auto-categorisation of bank transactions, and a calm "assistant" view of where
the money's going. Runs as a Home Assistant add-on with Ingress, persistent
SQLite storage, and HA notification integration.

## Features

- **Cash Flow Stress Meter** — green / amber / red tier based on the next 30 days
- **Calendar with running balance** — Quicken-style view with day-level click-throughs
- **Scheduled bills engine** — recurring (weekly/fortnightly/monthly/quarterly/yearly) or one-off, with optional end date or occurrence cap (e.g. "4 weeks of Afterpay")
- **Scheduled transfers** — first-class concept for money moving between your own accounts, with smart netting in the forecast
- **Smart suggestions** — transfer recommendations to avoid forecast lows
- **Debt attack order** — situation-aware ranking that respects deductibility & expiry timing
- **CSV upload** — Bendigo format auto-detected, generic fallback for other Aussie banks
- **Rule-based categorisation** — runs entirely locally, no AI needed (AI optional)
- **Category autocomplete** — remembers categories you've used so they're consistent
- **HA notifications** — bill reminders, morning digest, forecast alerts
- **Add any account** — Bendigo, Bankwest, CommBank, etc.
- **Dedup on CSV upload** — re-upload overlapping CSVs safely; only new transactions added

## Concepts

The dashboard models five entity types — getting these right matters for accurate forecasts.

### Accounts

Each account has a **bank**, **name**, **type** (transaction / savings / credit / loan / ppor), and balance values:

- **Balance** — what the bank shows as the current balance
- **Available** — what you can actually spend right now (factors in pending holds and authorisations). For transaction and credit accounts, the dashboard treats this as the *primary* number because it's what's actionable
- **Available redraw** (loans only) — extra you've paid into the loan that you could pull back out. Treated as an emergency cushion, not regular spending money

### Bills

Money leaving your accounts — bills, fees, subscriptions, salary (in reverse, as income).

- **Recurrence**: once / weekly / fortnightly / monthly / quarterly / yearly
- **End date** (optional): stop generating after this date
- **Occurrences remaining** (optional): stop after N more (useful for fixed instalments like Afterpay, BNPL)
- **Type**: bill (money out) or income (money in)

### Transfers

Money moving *between* two of your own accounts — mortgage payments, savings sweeps, redraw withdrawals.

This is different from a bill. A bill leaves your overall world. A transfer just moves money around. The forecast calendar nets transfers based on which accounts you've selected:

- Source selected, destination not → applied as money out (red)
- Destination selected, source not → applied as money in (green)
- Both selected → invisible (no net effect on your selected-account balance)
- Neither selected → ignored

This means your fortnightly mortgage payment correctly *reduces* your spendable cash when only the Income & Bills account is selected, but if you select both Income & Bills *and* the mortgage account, the running balance is unchanged.

### Transactions

Actual money movements imported from CSV. They populate the "Recent activity" panel and feed the categorisation engine.

### Interest-free plans

Tracked separately on credit cards (Latitude Gem Visa, Afterpay-style products) so the dashboard can warn about plans expiring before they roll onto the high "expired plan" rate.

## Installation

1. Go to **Settings → Add-ons → Add-on Store**
2. Click **⋮ menu → Repositories**
3. Paste the GitHub repo URL
4. Click **Add → Close**
5. Refresh the store, scroll to **"Hepburn Finance"** → **Install**
6. Click **Start**, then **Open Web UI**

## Configuration

After install, click **Configuration**:

| Field | Description |
|-------|-------------|
| `family_name` | Used in the greeting (e.g. "Hepburn") |
| `primary_user` | First family member name |
| `secondary_user` | Second family member name (optional) |
| `ai_provider` | `none` (default), `claude`, or `gemini` |
| `ai_api_key` | API key if you want AI-powered features |
| `notify_service` | HA notify service name, e.g. `mobile_app_lukes_phone` |
| `morning_digest_time` | When to send the daily summary |
| `bill_reminder_days` | Days ahead to alert on upcoming bills |

## First-run setup

The add-on seeds itself with placeholder Bendigo + Latitude accounts. Replace the
seeded values with your actuals in this order — each step makes the dashboard
more accurate.

**1. Update real balances** — click any account card's pencil icon. Update:
- Balance (current bank balance)
- Available (what's actually spendable today — most banks show this prominently)
- Available redraw (for mortgages, if you have any)

**2. Add your salary income** via **+ Add bill**:
- Type: **Income**
- Description: `Council pay`, `Roosters pay`, etc.
- Amount: net (after-tax) amount that lands in the account
- Repeats: typically fortnightly
- Category: e.g. `Income · Luke`, `Income · Peta` (autocomplete remembers)

**3. Add your recurring bills** via **+ Add bill** (one at a time):
- NRMA, Red Energy, Vodafone, NIB, Strata, water, council rates, etc.
- Use the autocomplete to keep category names consistent

**4. Add your transfers** via **+ Transfer**:
- Mortgage payments (Income & Bills → mortgage account)
- Savings sweeps (Income & Bills → savings)
- Credit card payments (Income & Bills → credit account)

**5. Upload bank CSVs** via **Upload CSV**:
- Bendigo CSVs are auto-detected
- Other banks fall through to a generic parser
- Re-uploading overlapping date ranges is safe — duplicates are skipped via fingerprint matching

## CSV upload deduplication

Every imported transaction gets a fingerprint = SHA-256 of `account|date|amount|description|reference`. The fingerprint is stored uniquely. Re-uploading the same CSV (or one that overlaps with already-imported dates) silently skips duplicates and reports the count.

This means you can:
- Upload month-by-month historical exports without worrying about overlap
- Re-import the same export to recover from corruption
- Combine exports from web vs app interfaces (assuming same description text)

## Database

SQLite at `/data/finance.db`. Persists across restarts/updates. Backed up
automatically as part of HA's snapshot via the Google Drive Backup add-on
(if installed).

## Updates

When new versions are pushed to GitHub, HA shows "Update available" on the
add-on. Click Update — your data is untouched. Schema migrations run
automatically; no manual intervention required.

## Disclaimer

This dashboard provides general financial information based on your data. It
is not personal financial advice. For investment, tax, or hardship decisions,
consult a licensed advisor.
