# Dune: Awakening — Base Calculator

A lightweight web app and data scraper to plan your base in Dune: Awakening. It helps you pick placeables, track required materials, power, water capacity, and auto-calculate consumables for a target number of days.

## Features

- Search and filter placeables by tier
- Deep Desert discount toggle (50%)
- Power Budget widget (available vs used)
- Water Capacity widget
- Apartment Planner (quick-add common materials per apartment)
- Consumables (Auto): enter days, it computes total consumables needed for selected equipment
- Selected items list and required materials summary
- Export/Import your planner state to a JSON file

## Project layout

- `get_data.py` — scraper that builds `items_data.json` from the community wiki
- `base_calc_v2.html` — the calculator UI (loads `items_data.json`)
- `items_data.json` — generated data file (created by `get_data.py`)

## Prerequisites

- Python 3.9+ (tested with 3.11)
- Packages: `requests`, `beautifulsoup4`

Install packages (optional venv recommended):

```powershell
python -m pip install --upgrade pip
pip install requests beautifulsoup4
```

## Generate data

- Fetch fresh data from the wiki (recommended for first run):

```powershell
python .\get_data.py --update
```

- Or reuse/merge existing data without external polling (only merges manual items and ensures minimal consumable items are present):

```powershell
python .\get_data.py
```

This writes `items_data.json` in the project root.

## Run the UI locally

Serve the folder and open the UI in a browser (so the JSON loads via HTTP):

```powershell
# Start a simple local server in the project directory
python -m http.server 8000
```

Then open:

- http://localhost:8000/base_calc_v2.html

## Using the app

1. Use search and tier filter to find items; click +/− to adjust counts
2. Toggle Deep Desert to apply 50% material discount
3. Apartment Planner lets you add per-apartment materials in bulk
4. Set "Run equipment for: [days]" and the app auto-calculates needed consumables
   - If an item has multiple consumables, the most efficient (longest burn time) is used
5. Export state to a dated JSON file or Import it later to continue planning

## Export / Import

- Export creates a file like `dune_base_YYYY-MM-DD.json` containing:
  - Deep Desert toggle, target days, selected item counts, and apartment planner values
- Import loads the same and repopulates the UI so you can tweak further

## Notes on data scraping

- The scraper targets Placeables pages and extracts: build cost, power, water capacity, and consumables
- Some items are added/overridden manually (e.g., Pentashield) for completeness
- Consumables can come from tables like "Consumable | Burn Time"; the scraper parses these into `{ Name, Hours }`

## Contributing / Issues

- Bug reports and feature requests: https://github.com/sylvainsf/dune-base-calculator/issues
- PRs welcome; please include a short description and testing notes

## License

Creative Commons Attribution-NonCommercial 4.0 International (CC BY-NC 4.0) — see `LICENSE`.
