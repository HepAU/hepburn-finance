# Changelog

All notable changes to Hepburn Finance.

## [0.3.0] — 2026-04-29

The Home Assistant integration release. Cash flow status and upcoming bills are now native HA sensors you can put on any Lovelace dashboard, automate against, or notify on.

### Added
- **HA native sensors** (10 of them) auto-pushed every 5 minutes:
  - `sensor.hepburn_finance_cash_today` — spendable cash today
  - `sensor.hepburn_finance_balance_30d_low` — lowest 30-day forecast
  - `sensor.hepburn_finance_days_until_zero` — days until balance hits zero
  - `sensor.hepburn_finance_stress_tier` — green/amber/red status
  - `sensor.hepburn_finance_next_bill_amount` — next bill (with full bills_list attribute for Lovelace)
  - `sensor.hepburn_finance_bills_7d_total`, `_bills_14d_total`, `_upcoming_bills_count`
  - `sensor.hepburn_finance_debt_total`, `_redraw_total`
- **Lovelace card snippets** documented in `HA_DASHBOARD.md` and surfaced via the new `/ha-dashboard` page (linked from the dashboard topbar). Includes ready-to-paste YAML for upcoming bills, cash flow summary, glance, and stress-tier button card.
- **Manual refresh endpoint** `/api/ha-refresh` to trigger an immediate sensor push (useful after a CSV upload or bulk edit).
- **Permission required:** `homeassistant_api: true` is now in the add-on config. HA will prompt you to approve this on update.

### Changed
- **Account type tags are sentence-case now** — "Borrowed", "Personal loan", "Investment", "Home loan", "Credit card", "Everyday", "Savings". Tighter pill shape (rounded), still colour-coded by type. Replaces the all-caps ALL_CAPS_TYPES rendering from v0.2.x.

### Note on the migration
The v0.2.1 cleanup migration that scrubs literal `"None"` strings out of the database is included in this release. If you skipped v0.2.1, those will be cleared automatically on first start of v0.3.0.

## [0.2.1] — 2026-04-29

### Fixed
- **"None" appearing under account balances.** When the account form rendered an existing account, Jinja was outputting the literal string "None" into `value=""` attributes for empty optional fields (account_number, nickname, notes). Re-saving the form persisted those literal strings to the database, which then displayed as "None" on the account cards. Fixed the template to handle null values properly, plus a one-time cleanup migration sweeps any existing "None" strings out of the database on first start.
- **Account type labels** for the new types now render properly: `loan_personal` → "PERSONAL LOAN", `loan_informal` → "BORROWED", `loan_investment` → "INVESTMENT LOAN", with appropriate colour tags. Previously they showed the raw type name.

## [0.2.0] — 2026-04-29

The transactions release. Click any transaction to edit it, bulk-tag similar ones, internal transfers auto-detect on CSV upload, and account types now include personal/informal loans alongside the existing investment loan and PPOR.

### Added
- **Transactions browser** at `/transactions` — filter by description, category, or account. Click any row to edit.
- **Transaction edit page** — fix description, amount, category, mark as internal transfer, add notes, delete. Editing flips `user_categorised` so it sticks even if rules change later.
- **Bulk-tag similar transactions** — when editing one transaction, if there are other untagged transactions with similar descriptions, a checkbox offers to apply the same category to all of them at once.
- **Internal transfer auto-detection on CSV upload** — pair-matching by date + magnitude + opposite signs in different accounts. Hardcoded recognition of Bendigo sub-account reference numbers (00571644691402-405) so untagged Peta transfers get categorised correctly. Both legs marked as `Transfer · Internal`, excluded from spending totals.
- **Past transactions on calendar** — past days now show an aggregated count and total of the day's spending (e.g. "3× $148"), separate from the future forecast.
- **Reconciliation page** at `/accounts/<id>/reconcile` — compares manual balance to sum of imported transactions. Useful sanity check after CSV imports. Access via the ⚖ icon on any account card on hover.
- **Three new account types**: `loan_personal` (solar, car, BNPL — non-deductible), `loan_informal` (borrowed from family/friends/work), and `loan_investment` (renamed from old `loan` — tax-deductible). Existing `loan` accounts auto-migrate to `loan_investment`.

### Changed
- **Recent activity panel** on dashboard now hides internal transfers (they were noise — your transactions always look the same direction in/out without context).
- **Recent activity rows are now clickable** — open the transaction edit page directly.
- **Debt attack panel** distinguishes personal, informal, and investment loans separately. The deductibility footnote applies only to investment loans now, not all loans.

### Migration
- Schema migration handles old databases automatically. Existing `loan`-typed accounts become `loan_investment`. After upgrade, manually re-classify any that should be `loan_personal` or `loan_informal` (e.g. Keith Howieson, PVRSC if those weren't actually investment properties).
- Dropped strict `CHECK` constraint on account `type` — application-side validation now, future-friendly.

## [0.1.4] — 2026-04-29

### Fixed
- **Investment loan rate display.** Loans showed "~6.0%" even when interest rate was set to 0 or another value. The fallback (`acc.interest_rate or 6`) treated 0 as falsy. Now reads the actual rate, with a clearly-marked estimate (`~6.0% *`) only when no rate is set at all.

### Added
- **+ Afterpay shortcut button** on the dashboard topbar. Opens a quick form for total amount + first instalment date + store name, then creates four (or 2/3/5/6/8) fortnightly one-off bills in one click. Each instalment is named distinctly ("Afterpay · Cotton On (1 of 4)") so they show up individually on the calendar and fall off as they're paid.

## [0.1.3] — 2026-04-29

### The big stuff
- **Forecast now uses available balance, not bank balance.** This was a critical bug — the forecast was overstating spendable cash by including pending holds. Fixed.
- **Transfers as a first-class concept.** Money moving between your own accounts (mortgage payments, savings sweeps, redraw) is now modelled separately from bills. The forecast calendar nets transfers correctly based on which accounts you've selected:
  - Source-only selected → applies as money out
  - Destination-only selected → applies as money in
  - Both selected → invisible (no net effect)
  - Neither selected → ignored
- **Available redraw** added to loan accounts. Tracked as an emergency cushion. Shown on the account card and noted in the debt attack panel.

### Bills got smarter
- **End date** field — set when a bill stops (e.g. cancelling Vodafone in July)
- **Occurrences remaining** field — useful for fixed-instalment things like Afterpay (e.g. "4 weeks of $89")
- **Past dates blocked** for one-off bills (recurring is fine, of course)

### Categories
- **Autocomplete on the category field** — datalist of every category you've already used (across rules, bills, transfers, transactions). Type to filter, click an existing one or type something new

### Account cards
- **Available shown prominently**, balance shown small underneath. For credit cards, available credit is now the headline number
- Loan accounts show **redraw** as a secondary line when set
- Pencil icon on each account card opens the edit form

### Calendar
- **Transfer events** show in blue, separate from bill (red) and income (green)
- Day click popover supports adding either a bill or a transfer
- Combined event markers when a day has multiple kinds (e.g. income + bill + transfer)

### Upload feedback
- CSV upload now reports duplicates skipped count clearly, with a note explaining the fingerprint-based dedup

### Migrations
- Schema migrations run automatically on first start of v0.1.3. No data loss for users upgrading from v0.1.2

## [0.1.2] — 2026-04-29

- Edit pencil icons on account cards and upcoming bills (single-click body still toggles forecast inclusion)
- Fixed a 500 error opening edit forms (added a global context processor for `cfg`)

## [0.1.1] — 2026-04-29

- **Critical fix:** dashboard CSS was 404'ing because URLs didn't include the HA Ingress path prefix. Added `IngressMiddleware` to set Flask's `SCRIPT_NAME` from the `X-Ingress-Path` header HA sends with every request.

## [0.1.0] — 2026-04-29

- Initial release
- Bendigo CSV parser with auto-detection
- Stress meter, smart suggestions, situation-aware debt attack
- Calendar with running balance forecast
- Rule-based categoriser (60+ default rules)
- Latitude Gem Visa pre-seeded with 8 interest-free plans
- All 9 Bendigo accounts pre-seeded
