# Hepburn Finance Add-ons

Home Assistant add-on repository for the Hepburn family finance dashboard.

## Add-ons in this repository

- **[Hepburn Finance](./hepburn_finance/)** — Family finance dashboard with cash flow forecasting, scheduled bills & transfers, and AI-assisted insights. See the add-on's [README](./hepburn_finance/README.md) for full feature details, and [CHANGELOG](./hepburn_finance/CHANGELOG.md) for version history.

## Installation

1. In Home Assistant, go to **Settings → Add-ons → Add-on Store**
2. Top-right **⋮ → Repositories**
3. Add: `https://github.com/HepAU/hepburn-finance`
4. Refresh the store and install the add-on under "Hepburn Finance Add-ons"

## Updates

Push changes to `main`. HA detects the version bump in `hepburn_finance/config.yaml` and shows "Update available" on the add-on page. Click Update — your data is preserved through schema migrations.

## Repository structure

```
hepburn-finance/
├── repository.yaml          ← marker file telling HA this is a custom repo
├── README.md                ← this file
└── hepburn_finance/         ← the add-on itself
    ├── config.yaml          ← HA add-on manifest (version, options, ports)
    ├── Dockerfile
    ├── build.yaml
    ├── README.md            ← add-on documentation
    ├── CHANGELOG.md         ← version history
    ├── app/                 ← Flask application
    │   ├── main.py
    │   ├── database.py      ← SQLite schema + migrations
    │   ├── forecast.py      ← Cash flow forecasting (bills + transfers)
    │   ├── stress.py        ← Stress meter + smart suggestions + debt attack
    │   ├── categoriser.py   ← Rule-based categorisation
    │   ├── parsers/         ← CSV parsers (Bendigo + generic fallback)
    │   ├── routes.py        ← Flask blueprint
    │   ├── templates/       ← Jinja2 HTML
    │   └── static/          ← CSS
    └── rootfs/              ← s6 service runner files
```
