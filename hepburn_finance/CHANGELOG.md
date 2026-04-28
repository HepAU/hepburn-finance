# Changelog

All notable changes to Hepburn Finance.

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
