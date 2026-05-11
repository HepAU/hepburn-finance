# Changelog

All notable changes to Hepburn Finance.

## [0.6.8] — 2026-05-11

### Added
- **Source/destination tag on internal transfers in the transactions list.** Paired internal transfers now show a small "from <Account>" tag next to the Internal badge for incoming rows, or "to <Account>" for outgoing rows. Direction is inferred from the amount sign. Only appears when the transfer has a paired counterpart in the database — un-paired Internal transactions (auto-detected but not linked) silently skip the tag.

### How
Added a LEFT JOIN through `transfer_pair_id` to fetch the paired transaction's account in the same query, so the list view knows which account is on the other side without N+1 lookups. Performance impact negligible (existing index on transfer_pair_id).

## [0.6.7] — 2026-05-02

### Added
- **"Show uncategorised only" toggle on the transactions page.** A prominent pill above the filter form shows the count of unreviewed transactions and toggles in-or-out of uncategorised-only view. Uses an amber dot when transactions need attention, switches to "✓ All transactions are categorised" when zero. The filter combines correctly with account/category/search filters and is preserved across edit→save→list redirects (so you can grind through the queue without losing your place).

### How "uncategorised" is determined
A transaction is considered uncategorised when its category is NULL/empty/'Uncategorised' AND `user_categorised` is 0 or NULL. So once you manually tag a transaction (even back to "Uncategorised" deliberately), it disappears from this view — the user_categorised flag means "user has reviewed this".

## [0.6.6] — 2026-05-02

### Added
- **+ Plan button in dashboard top action bar** alongside + Transaction, + Afterpay, + Transfer, + Budget, + Add bill. Direct one-click route to add an interest-free plan (Latitude Gem Visa, GO Mastercard, etc.) without needing to find the Plans page first.

## [0.6.5] — 2026-05-02

UX polish: account ordering reflects daily-use priority, plus mobile-specific layout adjustments.

### Changed
- **Account ordering now prioritises frequency-of-use, not alphabetical type.** Within each bank, accounts now appear in this order: transaction → credit → savings → informal loans → personal loans → owner-occupier → investment loans. So Card Account and Income & Bills surface first; mortgages move to the bottom of their bank group.
- **Bank groups themselves now ordered by their highest-priority account.** Banks containing your transaction accounts surface first; banks with only mortgages or only loans appear later.

### Added
- **Mobile-first layout adjustments** at viewport ≤720px:
  - Calendar hidden (12 cells × 7 columns is unscannable on a phone — swipe to dashboard for the popup)
  - Debt attack panel hidden (reference-only, takes vertical space)
  - Visa expandable summary hidden (large block, tap from accounts to see)
  - Stress card and budgets card padding tightened
  - Action buttons (+ Transaction / + Afterpay etc) scroll horizontally instead of wrapping
  - Greeting and tier text sized down for readability
- **Desktop layout unchanged** — the full dashboard is preserved when viewport is >720px.

### Notes
This is a CSS-only mobile change — no separate mobile dashboard, no separate routes. If you load the same URL on phone vs desktop, you get appropriate layouts automatically. Sections aren't *deleted* on mobile — they're just hidden via display:none, so they remain in the DOM and re-appear when the viewport widens (e.g. landscape mode on tablet).

## [0.6.4] — 2026-05-02

Three bug fixes from real-world usage.

### Fixed
- **Smart suggestion action buttons no longer 404 under HA Ingress.** Previously the buttons emitted absolute paths like `/transactions` which resolved to Home Assistant root (404) rather than the addon's ingress-prefixed URL. Now suggestions emit endpoint name + kwargs and the dashboard template uses Flask's `url_for` to build the correct URL — works both bare and under any Ingress path.
- **Debt attack order now reflects real-time balances.** Previously the debt list pulled from the legacy `balance` column, so informal-loan repayments posted via the transfer-with-destination flow (e.g. paying back Keith Howieson) showed in the account card but not in the debt attack list. Now uses `computed_balance` (opening_balance + transactions sum) consistently — same source of truth as everywhere else.
- **Spending budgets card now spans full width.** Was inheriting `.form-card` styling which capped width at 600px and centred it. Now uses a dedicated `.budgets-card` style that fills the available width — fits more budgets across without scrolling.

### Note
The action-button fix means existing suggestions in your dashboard will need a fresh page load to pick up the corrected URLs. After the addon update, refresh the dashboard once and the buttons will work.

## [0.6.3] — 2026-05-02

Hotfix: calendar was broken once any spending budget existed.

### Fixed
- **Calendar rendered as empty (no dates, no pills, no balances) when budgets were active.** Root cause: the `/api/forecast` endpoint was hard-accessing `i['id']` on every instance, but budget-drain instances don't carry an id. That raised `KeyError`, the API returned 500, the JS fetch failed silently, and the calendar's render data stayed null.
- Now using `.get()` for optional fields (id, category, recurring) so budget instances pass through cleanly.

### Visible result
After updating, the calendar will render again with the dashed amber budget pills appearing on future days as designed.

## [0.6.2] — 2026-05-02

Tiny but real fix: the dashboard had no nav to `/budgets/new` from a fresh state.

### Added
- **+ Budget button** in the dashboard top action bar, alongside + Transaction, + Afterpay, + Transfer, + Add bill. Direct one-click route to create your first budget without needing a Smart Suggestion to fire or knowing the URL.

### Note
The "Manage →" link on the Spending budgets card already existed but was hidden when no budgets were present (chicken-and-egg). The action bar button solves that.

## [0.6.1] — 2026-05-02

Closing the loop on smart suggestions. The action buttons in the Smart Suggestions panel now actually do something — previously they were styled like buttons but didn't link anywhere.

### Added
- **"Set cap" suggestion now creates a budget.** When the discretionary drift module fires (e.g., "Takeaway up 47%, try a $300/month cap"), tapping "Set cap" navigates to the new budget form with name, category, weekly amount, and cadence pre-filled. One click, one save, budget active.
- **All other suggestion buttons now wired to action URLs:**
  - "Plan transfer" (cashflow gap) → `/transfers/new` with source account and amount pre-filled
  - "Plan it" (bill cluster) → `/transfers/new` to set up a top-up
  - "Schedule payment" (plan expiry) → `/plans` (the management page)
  - "Review subs" / "Cancel idle" (subscription audit) → `/transactions?cat=Subscriptions` filtered view
  - "See transactions" (trend up/down) → `/transactions?cat=<category>` filtered view

### Changed
- **Suggestion buttons render as anchor links when an action URL exists**, with accent border/colour to signal they're tappable. Suggestions without an action URL keep the old disabled-button look (faded).

### Note
Suggestions you don't tap have no effect — they're informational. The "Set cap" flow doesn't auto-create budgets in the background; it just walks you to a pre-filled form so you can review the numbers and confirm.

## [0.6.0] — 2026-05-02

The spending budgets release. A new first-class concept alongside bills and transfers, designed to model the discretionary spend that doesn't fit the "scheduled bill on a specific day" pattern — groceries, fuel, takeaway, fun money. Budgets drain the forecast smoothly across each period, factoring in real spending so you don't get double-counted.

### Added
- **Spending budgets** — weekly (Mon–Sun), fortnightly, or monthly amounts attached to a category and a target account. CRUD UI at `/budgets` with a list view (progress bars per budget) and add/edit/delete.
- **Forecast integration** — each future day in a budget's period gets a smoothed daily drain on the target account. As real transactions in the budget's category land, the projected drain shrinks for the rest of the period (so you never double-count).
- **Calendar visual** — dashed amber pills on future days showing the day's projected budget drain (collapsed into one "~$X budget" pill per day rather than a pill per budget).
- **Dashboard "Spending budgets" card** — progress bars per budget showing $ spent, $ left, % used, and days remaining in the current period. Bars colour-code green/amber/red as you approach the cap.
- **HA popup section** — between the cash headline and bills list. Shows each budget with its progress bar and dollars remaining. Hidden if no budgets exist.
- **HA sensor** `sensor.hepburn_finance_budgets_remaining` — total remaining across all budgets, with `budgets` attribute containing per-budget data for the popup.

### Fixed
- **HA popup now includes scheduled transfers** alongside bills. Previously: a fortnightly mortgage transfer was missing from the popup, so the running balance under each section was wrong. Now: transfers with a non-zero net effect on selected accounts (e.g. mortgage payments to an unselected mortgage account) appear in their day's section, and the running balance reflects them.

### Schema migration
- New `spending_budgets` table created automatically. No data loss for existing installs. Forward-compatible.

### Notes
- Budgets only apply when you select the target account in the dashboard's "include in calendar forecast" view. If the account isn't selected, the budget doesn't show in the forecast (because the forecast is for selected-accounts cash flow).
- The "Set cap" suggestion button (from v0.5.0's discretionary drift module) currently doesn't auto-create a budget yet — that wiring lands in v0.6.1.

## [0.5.8] — 2026-05-02

Two bug fixes addressing real workflow friction.

### Fixed
- **Filtered transaction list now preserves the filter when editing.** Previously: filter to one account, click edit on a transaction, save → list reverts to showing all accounts. Now the filter (account, category, search query) persists through edit/save/delete via hidden form fields. Same for the "Cancel" and "← All transactions" links — they go back to the filtered list, not the unfiltered one.
- **Override balance "as-of date" now uses end-of-day semantics.** Before: typing "01/05/2026" deleted transactions from 30/04/2026 (treated as "balance at start of 1 May, 1 May not yet counted"). Now: same input is interpreted as "balance is correct at end of 1 May" — transactions on 1 May and earlier are deleted (already baked into the new figure), transactions on 2 May and later roll forward.

### Changed
- **Override balance form re-labelled for clarity.** The field was "As-of date" with no help text. Now: "Balance is correct at end of [date]" with explicit help text and a yellow callout explaining what gets deleted vs kept. Defaults to today's date instead of being empty.

## [0.5.7] — 2026-05-02

Half-transfer fix. When you mark a CSV-imported transaction as an internal transfer to an account that doesn't get its own CSV (informal loans, payday-day mortgage payments, etc.), the dashboard now creates the matching transaction on the destination so its balance updates correctly.

### Added
- **Destination account picker on the transaction edit form.** When you tick "Internal transfer", a dropdown appears letting you pick the destination account. On save, the dashboard creates a matching credit transaction on that account, linked back to the original via `transfer_pair_id`. Leave the dropdown blank to behave as before (just tag, no counterpart).
- **Use case:** repaying Keith Howieson (informal loan) — the outgoing $500 in your Income & Bills CSV now reduces Keith's debt by $500 because the dashboard auto-posts the credit there.
- **Use case:** mortgage payments where the mortgage account isn't imported separately — the outgoing transaction in your everyday account reduces the mortgage balance automatically.

### Changed
- **Transaction delete cascades to paired half.** Deleting one side of a paired internal transfer now also deletes the other half. No more orphaned counterparts after edits.
- **Re-editing the destination on a paired transfer** works correctly — old counterpart is deleted, new one created in the right place.

### Note
This only affects newly-edited transactions. To apply to existing manually-tagged transfers, just edit each one and pick the destination account.

## [0.5.6] — 2026-05-02

Critical bug fix in the balance computation model.

### Fixed
- **Computed balance no longer double-counts internal transfers.** Previously, the v0.4.0 balance model excluded `is_internal_transfer=1` transactions from the sum. But this was wrong: the user's `opening_balance` already represents the actual bank balance at a point in time, which inherently includes the effect of every transaction — including transfers. By excluding internal transfers from the sum, those amounts were effectively being added back to the displayed balance, inflating it.
- **Visible symptom this fixes:** computed balance showing $8,000+ when reality is more like $6,000, where the difference equals the sum of internal transfers in the account history.

### Changed
- `compute_account_balance` and `hydrate_accounts` now sum **all** transactions, including those marked as `is_internal_transfer`. The flag is still used (correctly) for spending analytics and category trends — so transfers don't show as "spending" — but no longer interferes with balance computation.

### Note on internal transfer detection accuracy
The auto-detector pairs transactions across accounts. If only some accounts are imported, the detector can produce both false negatives (real transfers not detected because the matching leg isn't loaded yet) and rare false positives. Neither affects balance computation any more after this fix, but you may want to use the bulk-edit feature on `/transactions` to clean up obvious mis-tags.

## [0.5.5] — 2026-05-02

Hotfix for v0.5.4. The fix for stale `available` values was correctly applied in the Python layer (`hydrate_accounts`, `get_starting_balance`) but the dashboard template was still using its own logic that bypassed `display_balance` and showed `available` as the headline.

### Fixed
- **Account cards now show the computed balance as the headline** (transactions-driven), with the bank's `available` figure shown as a small secondary "Bank shows $X" line — only when the two differ. Previously: $200.80 headline (stale available), $8,259.37 secondary "Balance" (computed). Now: $8,259.37 headline, "Bank shows $200.80" secondary if drift exists.
- **Cash today and forecast** were already correct in v0.5.4 — only the visual cards were misleading.

## [0.5.4] — 2026-04-30

Bug fix: balances and forecast now correctly reflect uploaded transactions.

### Fixed
- **Calendar forecast and account cards now use the most accurate balance source.** Previously, if `available` (the bank's "spendable now" figure) was set on an account, it would always be preferred — even if you'd subsequently uploaded transactions that made `opening_balance + transactions` more accurate. Now: when transactions exist, the computed balance wins. When they don't (fresh account), `available` is still used as the freshest manual snapshot.
- **Visible symptom this fixes**: yesterday's income shows in transaction history, but calendar days going forward show stale figures and don't reflect the deposit. This is now resolved — the forecast walks forward from the true computed balance.

### Why this matters
Before this fix, the `available` field acted as a "stuck" override. Setting it once meant the dashboard used it indefinitely, ignoring fresh transaction data. After this fix, `available` is used only as a fallback when no transactions exist for an account — once you start uploading CSVs, computed balance takes over.

## [0.5.3] — 2026-04-30

Polish release for the HA popup and dashboard. Three small but meaningful fixes.

### Fixed
- **Australian date format everywhere.** Stress messages now read "Forecast goes negative — $-1,142 on Sat 30 May" instead of an ISO date. The lowest-balance figure under "Lowest in 30 days" reads "Sat 30 May". Both server-rendered and pushed to HA.
- **HA popup running balance carries across sections.** The "Balance after Today" figure now correctly factors into the "Balance after This week" calculation, instead of resetting to today's cash for each section. Means if Tomorrow leaves you in the red, This week's row reflects that running shortfall instead of pretending you started fresh.
- **HA popup section labels include their date range.** No more guessing what "This week" means: it now reads "THIS WEEK · Sat 2 — Sun 4 May" so you know exactly which days are bucketed in there. Today and Tomorrow show the specific date.
- **HA popup "Open Finance dashboard" button centred.** Was left-aligned before, now sits in the middle of the footer.

## [0.5.2] — 2026-04-30

The clarity release. The dashboard header now tells one story instead of three competing ones, manual transactions are supported for non-CSV creditors, interest-free plans have a proper management UI, and the demo-data banner remembers when you've reviewed it.

### Changed
- **Dashboard header redesigned.** The "Cash Flow / Days Until Zero / Bills Coverage" three-stat grid was replaced with a single narrative + three plain-dollar supporting figures (Lowest in 30 days, Today's cash, 30-day bills due). The wording adapts to the tier: green ("Cash flow steady"), amber ("Tight period coming — you'll dip to $X on date Y, then recover"), red ("Likely shortfall — action needed today"). Removes the abstract "Bills Coverage 200%" metric that read more like a bank report than a kitchen-table summary.

### Added
- **+ Transaction button.** Manually add a single transaction to any account — useful for credit cards or accounts where CSV import isn't available (e.g. Latitude Gem Visa). The form has separate "Money out" / "Money in" controls so you don't have to remember which sign to use. Saved transactions are fingerprinted identically to imported ones, so they dedup correctly against future CSV uploads.
- **Interest-free plan management UI** at `/plans`. Add, edit, and delete plans through the dashboard rather than direct DB access. The list view shows current outstanding, monthly payment, expiry, and expired-plan rate. The dashboard's plan section now includes "Manage →" and "+ Add another plan" links. Empty state encourages adding one if there are credit accounts but no plans tracked yet.
- **Plan items on the dashboard are now clickable** — tap any active plan to edit it.

### Fixed
- **Demo data banner persistence.** Once you submit the cleanup form, a `seed_cleanup_completed=1` flag is set and the banner stays hidden — even if you chose "Leave alone" for some seed-named accounts (e.g. you renamed them and want to keep using them as real accounts). Visit `/admin/cleanup?force=1` to re-show seed-named items if needed.

### Migration
No schema changes. All v0.5.x databases are forward-compatible.

## [0.5.1] — 2026-04-30

The "make this safe to share" release. Removes all demo seed data from new installs and provides a one-shot cleanup tool for existing users to remove what was seeded earlier.

### Changed
- **`seed_initial_accounts()` is now a no-op.** Fresh installs start with zero accounts, zero interest-free plans, and the user's first action is "+ Add account". Public clones of the GitHub repository will not contain any pre-loaded financial data.

### Added
- **One-shot cleanup tool at `/admin/cleanup`.** Detects seed data still present from older v0.1.x installs and lets the user choose, per item:
  - Leave alone (default)
  - Keep account, wipe transactions (for a fresh start with the account itself)
  - Remove account + everything in it
  - For interest-free plans: keep or remove
- **Two-step confirmation**: must type `DELETE` exactly to confirm.
- **Done screen** showing exactly what was changed.
- **Dashboard banner** appears only when seed data is present, linking to the cleanup tool. Disappears once everything is cleaned.

### Notes for upgrading users
- Pandora and 7 other interest-free plans were originally seeded as part of the demo data. They were never on any uploaded statement. Use the cleanup tool to remove them.
- Interest-free plans are not auto-detected from CSV uploads — they're a Latitude-specific feature that doesn't appear in transaction exports. Add real ones manually if you want them tracked.

## [0.5.0] — 2026-04-30

The smart suggestions release. The "Smart suggestions" panel now actually thinks about your data instead of just shouting "you're going negative." Five distinct suggestion types working together to surface what matters.

### Added
- **Bill clustering detection** — when 3+ bills hit on the same day totalling >$300 *and* the forecast balance after them drops below the safe floor, you get a specific "X bills on Y day, biggest is $Z, call them to shift the date" suggestion. Identifies the largest bill in the cluster as the prime candidate to negotiate.
- **Subscription audit** — analyses transaction cadence over the last 90 days to find recurring small charges (≤$80, low variance, weekly/fortnightly/monthly spacing). Reports total monthly bleed and yearly cost. Requires 3+ confirmed hits to avoid false positives. Also flags "stale" subs (haven't been used in 60+ days but still being charged).
- **Category trend deltas** — last 30 days vs prior 60-day average. Flags categories that have moved >25% in either direction (with absolute floor of $40). "Up" trends are attention-flagged; "down" trends get a small celebratory note.
- **Discretionary drift detection** — focused on takeaway/delivery/entertainment/shopping. When recent spend in these is up >30%, suggests a concrete monthly cap halfway between recent and baseline, with weekly equivalent. "Try $350/month, that's $81/week, saves $254."
- **Suggestions module structure** — each suggestion type lives in `app/suggestions/<name>.py`. The public API `smart_suggestions(account_ids, today)` combines and priority-sorts them.

### Changed
- **Suggestion deduplication** — when discretionary drift fires for a category (e.g. Food · Takeaway), the generic trend module no longer re-reports it. The more specific cap suggestion takes priority.
- **Bill cluster suggestion** picks the biggest single bill in the cluster as the negotiation target. NRMA $220 hitting on the same day as Vodafone $108 → it suggests calling NRMA, not Vodafone.

### Migration
No schema changes. `app.stress.smart_transfer_suggestions()` kept as a deprecated stub that delegates to the new module — anything still importing the old path keeps working.

## [0.4.0] — 2026-04-30

The transactions-drive-balance release. Once you've set an opening balance for an account, transactions take over — uploading a CSV updates the displayed balance automatically. No more chasing two figures that disagree.

### Changed
- **Account balance is now computed**, not manually maintained. Each account has an `opening_balance` (set when you create the account) and the displayed balance is `opening_balance + sum(non-internal transactions)`. CSV uploads keep it current.
- **"Last updated" indicator** on each account card showing the date of the most recent transaction. Tells you at a glance whether the figure is fresh or stale.
- **The "Balance" field is gone from the edit form** for existing accounts. Replaced with a read-only "Current computed balance" display showing opening balance and last-tx date.
- **Override path** for opening balance is hidden in a `<details>` disclosure on the edit page. Use it only when reality has drifted (a bank fee that didn't import) or you want to start fresh — setting a new opening balance with an as-of date wipes earlier transactions.

### Added
- **HA bills_grouped attribute** on `sensor.hepburn_finance_next_bill_amount` — bills are pre-chunked server-side into Today / Tomorrow / This week / Next week / Later sections with subtotals. Used by the redesigned dashboard popup.
- **Calendar past-day click → real transactions**. Click any past day on the calendar and the popover now shows the actual imported transactions for that day, not just the sum.

### Migration
- Existing accounts get `opening_balance` seeded from their current `balance` value automatically. Computed balances will match what you saw in v0.3.0 immediately, then update as you upload CSVs.

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
