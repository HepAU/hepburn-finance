# Hepburn Finance · Lovelace card snippets

## How to use these

In your Home Assistant dashboard, edit the dashboard, click **+ Add card**, scroll to the bottom and pick **Manual**, then paste one of the YAML blocks below.

The sensors below are populated by the Hepburn Finance add-on once it's running with `homeassistant_api: true`. They refresh every 5 minutes, or immediately when you call `POST /api/ha-refresh` on the add-on.

---

## Compact "next bill" card

Just shows the next bill name, amount, and how many days away.

```yaml
type: entity
entity: sensor.hepburn_finance_next_bill_amount
name: Next bill
icon: mdi:receipt-text
state_color: false
```

---

## Upcoming bills (markdown)

Lists the next 8 bills with dates and amounts. Uses the `bills_list` attribute on the next-bill sensor.

```yaml
type: markdown
title: Upcoming bills
content: |
  {% set bills = state_attr('sensor.hepburn_finance_next_bill_amount', 'bills_list') or [] %}
  {% if bills | length == 0 %}
  _No bills in the next 14 days_
  {% else %}
  {% for b in bills %}
  - **{{ b.date }}** · {{ b.name }} · ${{ '%.2f' | format(b.amount) }}{% if b.days_away <= 3 %} ⚠️{% endif %}
  {% endfor %}
  {% endif %}
```

---

## Cash flow summary (entities card)

Shows the headline numbers: cash today, lowest forecast, days until zero, stress tier.

```yaml
type: entities
title: Hepburn Finance
entities:
  - entity: sensor.hepburn_finance_stress_tier
    name: Cash flow status
  - entity: sensor.hepburn_finance_cash_today
    name: Cash today
  - entity: sensor.hepburn_finance_balance_30d_low
    name: Lowest forecast (30d)
  - entity: sensor.hepburn_finance_days_until_zero
    name: Days until zero
  - entity: sensor.hepburn_finance_bills_7d_total
    name: Bills due this week
  - entity: sensor.hepburn_finance_bills_14d_total
    name: Bills due (14 days)
```

---

## Big-number stress tier card with conditional colour

Shows just the green/amber/red status word, big.

```yaml
type: custom:button-card
entity: sensor.hepburn_finance_stress_tier
name: Cash flow
show_icon: true
show_state: true
styles:
  card:
    - height: 100px
  state:
    - font-size: 28px
    - font-weight: 600
    - text-transform: uppercase
state:
  - value: green
    color: '#10b981'
    icon: mdi:check-circle
  - value: amber
    color: '#f59e0b'
    icon: mdi:alert
  - value: red
    color: '#ef4444'
    icon: mdi:alert-octagon
```

(Requires the `button-card` HACS card.)

---

## Vanilla version (no HACS)

Plain Lovelace, no extras.

```yaml
type: glance
title: Cash flow
entities:
  - entity: sensor.hepburn_finance_cash_today
    name: Cash
  - entity: sensor.hepburn_finance_days_until_zero
    name: Days left
  - entity: sensor.hepburn_finance_bills_7d_total
    name: Bills 7d
  - entity: sensor.hepburn_finance_balance_30d_low
    name: 30d low
```

---

## All sensors created

- `sensor.hepburn_finance_cash_today` — spendable cash today across selected accounts ($)
- `sensor.hepburn_finance_balance_30d_low` — lowest forecast over next 30 days ($)
- `sensor.hepburn_finance_days_until_zero` — days until forecast crosses zero, or 30 if never
- `sensor.hepburn_finance_stress_tier` — `green` / `amber` / `red` / `unknown`
- `sensor.hepburn_finance_next_bill_amount` — amount of next bill ($), with `name`, `date`, `days_away`, `bills_list` attributes
- `sensor.hepburn_finance_bills_7d_total` — total bills due in 7 days
- `sensor.hepburn_finance_bills_14d_total` — total bills due in 14 days
- `sensor.hepburn_finance_upcoming_bills_count` — count of bills in next 14 days
- `sensor.hepburn_finance_debt_total` — total debt across loans ($)
- `sensor.hepburn_finance_redraw_total` — total mortgage redraw available ($)

---

## Automations

You can build automations on top of these sensors. Example: notify when stress tier goes red.

```yaml
alias: Notify on cash flow stress
trigger:
  - platform: state
    entity_id: sensor.hepburn_finance_stress_tier
    to: red
action:
  - service: notify.mobile_app_lukes_phone
    data:
      title: Cash flow alert
      message: >
        Hepburn Finance flagged red:
        {{ state_attr('sensor.hepburn_finance_stress_tier', 'message') }}
mode: single
```
