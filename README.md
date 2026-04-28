# Hepburn Finance — Home Assistant Add-on

A family finance dashboard with cash flow forecasting, scheduled bill management,
auto-categorisation of bank transactions, and a calm "assistant" view of where
the money's going. Runs as a Home Assistant add-on with Ingress, persistent
SQLite storage, and HA notification integration.

## Features

- **Cash Flow Stress Meter** — green / amber / red tier based on the next 30 days
- **Calendar with running balance** — a Quicken-style view, click any day for details
- **Scheduled bills engine** — recurring (weekly/fortnightly/monthly/quarterly/yearly) or one-off
- **Smart suggestions** — transfer recommendations to avoid forecast lows
- **Debt attack order** — situation-aware ranking that respects deductibility & expiry timing
- **CSV upload** — Bendigo format auto-detected, generic fallback for other banks
- **Rule-based categorisation** — runs entirely locally, no AI needed (AI optional)
- **HA notifications** — bill reminders, morning digest, forecast alerts via the Companion app
- **Add any account** — Bendigo, Bankwest, CommBank, etc.

## Installation

This is a custom add-on installed via the Home Assistant Add-on Store:

1. Go to **Settings → Add-ons → Add-on Store**
2. Click the **⋮ menu (top right) → Repositories**
3. Paste your GitHub repo URL: `https://github.com/HepAU/hepburn-finance`
4. Click **Add → Close**
5. Refresh the store, scroll down to **"Hepburn Finance"** → click **Install**
6. Once installed, click **Start**, then **Open Web UI**

## Configuration

After install, click **Configuration** on the add-on page:

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

## First-run

The add-on seeds itself with the Bendigo + Latitude accounts observed during
initial development. **Update them** with your actual current balances via the
dashboard's account cards (click → edit). Add other accounts (Bankwest, etc.)
via the **+ Add account** card.

## Database

SQLite at `/data/finance.db`. Persists across restarts/updates. Backed up
automatically as part of HA's snapshot.

## Updating

When new versions are published to GitHub, HA shows "Update available" on the
add-on. Click Update — your data is untouched.

## Disclaimer

This dashboard provides general financial information based on your data. It
is not personal financial advice. For investment, tax, or hardship decisions,
consult a licensed advisor.
