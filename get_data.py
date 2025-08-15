import argparse
import json
import os
import re
import time
from urllib.parse import unquote

import requests
from bs4 import BeautifulSoup

# Configuration
BASE_URL = "https://awakening.wiki"
CATEGORY_URL = f"{BASE_URL}/Category:Placeables"
OUTPUT_FILENAME = "items_data.json"
USER_AGENT = "DuneAwakeningDataExtractor/4.0 (Python script; github.com/Gizmo3030/Dune-Awakening-API)"


def merge_manual_items(items):
    """Merge hard-coded manual items into the provided list by Name.
    If a manual item shares a Name with an existing one, manual overwrites.
    """
    manual = [
        {
            "Name": "Pentashield",
            "Recipe": [
                {"Name": "Calibrated Servoks", "Count": 6},
                {"Name": "Steel", "Count": 2},
                {"Name": "Cobalt Paste", "Count": 20},
            ],
            "Power": {"Provides": 0, "Consumes": 6},
            "WaterCapacity": 0,
        }
    ]
    by_name = {i.get("Name"): i for i in items}
    for m in manual:
        by_name[m["Name"]] = m
    return list(by_name.values())

def get_placeable_links(url):
    """Scrapes a category page to get links to all placeable items."""
    print(f"[links] Fetching placeable item links from {url}...")
    links = []
    headers = {'User-Agent': USER_AGENT}
    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        print(f"[links] HTTP {response.status_code}, bytes={len(response.content)}")
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Find the main content area for category pages
        content_div = soup.find('div', id='mw-pages')
        if content_div:
            # Find all list items within the category groups
            for li in content_div.find_all('li'):
                link = li.find('a')
                if link and link.has_attr('href'):
                    links.append(BASE_URL + link['href'])
        if not content_div:
            print("[links] WARNING: Could not find div#mw-pages; page structure may have changed.")
        
        print(f"[links] Found {len(links)} item links.")
        if links:
            preview = "\n       - ".join(links[:5])
            print(f"[links] First few links:\n       - {preview}")
        return links
    except requests.exceptions.RequestException as e:
        print(f"[links] ERROR fetching category page: {e}")
        return []

def _iter_following_until_next_heading(start_tag):
    """Yield siblings after start_tag until the next h2/h3 appears."""
    current = start_tag
    while current is not None:
        current = current.find_next_sibling()
        if current is None:
            break
        if current.name in ("h2", "h3"):
            break
        yield current


def _parse_quantity_and_name(text):
    """Extract (quantity, name) from a string like '10x Plank', 'Plank x10', 'Plank (10)'."""
    t = re.sub(r"\s+", " ", text).strip()
    # Patterns to try
    patterns = [
        r"^(?P<qty>\d+)\s*[xX]\s*(?P<name>.+)$",       # 10x Plank
        r"^(?P<name>.+?)\s*[xX]\s*(?P<qty>\d+)$",       # Plank x10
        r"^(?P<name>.+?)\s*\((?P<qty>\d+)\)$",         # Plank (10)
        r"^(?P<qty>\d+)\s+(?P<name>.+)$",               # 10 Plank
    ]
    for pat in patterns:
        m = re.match(pat, t)
        if m:
            name = m.group("name").strip()
            qty = int(m.group("qty"))
            return qty, name
    # Fallback: if it starts with a number, split on first space
    m = re.match(r"^(\d+)\s+(.*)$", t)
    if m:
        return int(m.group(1)), m.group(2).strip()
    return None, None


def _extract_int(text):
    m = re.search(r"\d+", text or "")
    return int(m.group(0)) if m else None


def parse_power_from_page(soup):
    """Parse power provided/consumed from infobox-like tables or text patterns."""
    provides = 0
    consumes = 0
    ambiguous_vals = []

    # Determine if this page looks like a generator (by title)
    page_title = ""
    h1 = soup.find('h1')
    if h1:
        page_title = h1.get_text(" ", strip=True)
    elif soup.title:
        page_title = soup.title.get_text(" ", strip=True)
    is_generator_page = 'generator' in (page_title or '').lower()

    # Table-based extraction: scan rows for power labels
    LABELS_CONSUME = [
        "power draw", "power consumption", "consumption", "consumes",
        "power required", "required power", "power requirement", "use", "usage"
    ]
    LABELS_PROVIDE = [
        "power provided", "power output", "power generation", "generates", "generated", "provided power", "generator output", "produces", "produced"
    ]

    for row in soup.find_all('tr'):
        # Get label and value cells
        label_el = row.find('th') or (row.find_all('td')[0] if len(row.find_all('td')) >= 2 else None)
        value_el = None
        tds = row.find_all('td')
        if len(tds) >= 2:
            value_el = tds[1]
        elif len(tds) == 1 and row.find('th'):
            value_el = tds[0]

        if not label_el or not value_el:
            continue

        label = label_el.get_text(" ", strip=True).lower()
        val_text = value_el.get_text(" ", strip=True)
        # If label clearly indicates provide, prefer that over consume
        if any(lbl in label for lbl in LABELS_PROVIDE):
            n = _extract_int(val_text)
            if n:
                provides = max(provides, n)
            continue
        if any(lbl in label for lbl in LABELS_CONSUME):
            n = _extract_int(val_text)
            if n:
                consumes = max(consumes, n)
            continue
        # Ambiguous generic 'power' label; collect for later decision
        if 'power' in label:
            n = _extract_int(val_text)
            if n:
                ambiguous_vals.append(n)

    # Text-based fallback
    page_text = soup.get_text(" ", strip=True)
    m_cons = re.search(r"power\s*(?:draw|consumption|required|requirement|use|usage)\s*[:=]?\s*(\d+)", page_text, flags=re.I)
    m_prov = re.search(r"power\s*(?:provided|output|generation|generated|produced|produces)\s*[:=]?\s*(\d+)", page_text, flags=re.I)
    if m_prov and provides == 0:
        provides = int(m_prov.group(1))
    if m_cons and consumes == 0 and not m_prov:
        consumes = int(m_cons.group(1))

    # Resolve ambiguous values if neither side detected via clear labels
    if ambiguous_vals and provides == 0 and consumes == 0:
        if is_generator_page:
            provides = max(ambiguous_vals)
        else:
            consumes = max(ambiguous_vals)

    print(f"[power] provides={provides}, consumes={consumes}")
    # Heuristic: if an item both provides and consumes, treat it as a generator (consumption likely misclassified)
    if provides > 0 and consumes > 0:
        print("[power] Heuristic applied: provides>0 and consumes>0; setting consumes=0 for generator.")
        consumes = 0
    return {"Provides": int(provides or 0), "Consumes": int(consumes or 0)}


def parse_water_capacity_from_page(soup):
    """Parse water capacity (in liters) from infobox-like tables or text.
    Heuristics:
      - Prefer table rows whose label mentions water/liquid and capacity/storage/volume.
      - Or generic capacity/storage rows whose value contains L/liter(s).
      - Fallback to text search near 'water/liquid ... capacity/storage/volume ... <num> L'.
    Returns an integer number of liters (default 0 if not found).
    """
    capacity_l = 0

    def _parse_liters(s: str):
        if not s:
            return None
        # Accept numbers with commas or decimals followed by optional L/liter keywords
        m = re.search(r"(\d[\d,\.]*)\s*(?:l\b|litre\b|liter\b|liters\b|litres\b)?", s, flags=re.I)
        if m:
            num = m.group(1).replace(",", "")
            try:
                return int(float(num))
            except ValueError:
                return None
        return None

    for row in soup.find_all('tr'):
        tds = row.find_all('td')
        label_el = row.find('th') or (tds[0] if len(tds) >= 2 else None)
        value_el = (tds[1] if len(tds) >= 2 else (tds[0] if len(tds) == 1 and row.find('th') else None))
        if not label_el or not value_el:
            continue
        label = label_el.get_text(" ", strip=True).lower()
        val_text = value_el.get_text(" ", strip=True)

        label_has_water = any(w in label for w in ("water", "liquid"))
        label_has_capacity = any(w in label for w in ("capacity", "storage", "volume", "tank"))
        val_mentions_liters = re.search(r"\b(l|liter|litre|liters|litres)\b", val_text, flags=re.I)

        liters = None
        if label_has_water and label_has_capacity:
            liters = _parse_liters(val_text)
        elif ("capacity" in label or "storage" in label) and val_mentions_liters:
            liters = _parse_liters(val_text)

        if liters is not None:
            capacity_l = max(capacity_l, liters)

    if capacity_l == 0:
        # Text-based fallback
        text = soup.get_text(" ", strip=True)
        m = re.search(
            r"(water|liquid)[^\n]{0,40}?(capacity|storage|volume)[^\n]{0,15}?(\d[\d,\.]*)\s*(l|liter|litre|liters|litres)",
            text,
            flags=re.I,
        )
        if m:
            try:
                capacity_l = int(float(m.group(3).replace(",", "")))
            except ValueError:
                capacity_l = 0

    print(f"[water] capacity_liters={capacity_l}")
    return int(capacity_l or 0)


def _parse_hours(text: str):
    """Parse a duration string like '1h', '2 hours', '30m' into hours (float)."""
    if not text:
        return None
    t = text.strip().lower()
    # minutes first
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:m|min|mins|minute|minutes)\b", t)
    if m:
        return float(m.group(1)) / 60.0
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:h|hr|hrs|hour|hours)\b", t)
    if m:
        return float(m.group(1))
    # compact like x1h or 1h without space
    m = re.search(r"x\s*(\d+(?:\.\d+)?)\s*h\b", t)
    if m:
        return float(m.group(1))
    m = re.search(r"(\d+(?:\.\d+)?)h\b", t)
    if m:
        return float(m.group(1))
    return None


def parse_consumables_from_page(soup):
    """Parse consumables for equipment: list of {Name, Hours}.
    Looks for table rows labeled Consumable(s)/Fuel/Lubricant/Upkeep and extracts names and hours.
    """
    results = []
    # Table-based: handle row-wise labeling and explicit two-column headers
    for tbl in soup.find_all('table'):
        headers = [th.get_text(' ', strip=True).lower() for th in tbl.find_all('th')]
        looks_like_consumable_table = any('consumable' in h or 'fuel' in h or 'lubricant' in h for h in headers) and any('burn' in h or 'time' in h or 'duration' in h for h in headers)
        for row in tbl.find_all('tr'):
            ths = row.find_all('th')
            tds = row.find_all('td')
            if ths and tds:
                # pattern: header + value in same row (infobox style)
                label = ths[0].get_text(' ', strip=True).lower()
                if any(k in label for k in ("consumable", "consumables", "fuel", "lubricant", "upkeep", "maintenance")):
                    value_el = tds[-1]
                    anchors = value_el.find_all('a')
                    seen = set()
                    if anchors:
                        for a in anchors:
                            nm = a.get_text(' ', strip=True) or a.get('title') or ''
                            if not nm or nm in seen:
                                continue
                            seen.add(nm)
                            ctx = value_el.get_text(' ', strip=True)
                            hrs = _parse_hours(ctx) or 1.0
                            results.append({"Name": nm, "Hours": float(hrs)})
                    else:
                        text = value_el.get_text(' ', strip=True)
                        hrs = _parse_hours(text) or 1.0
                        if text:
                            results.append({"Name": text, "Hours": float(hrs)})
            elif looks_like_consumable_table and len(tds) >= 2:
                # pattern: explicit columns: first col = consumable name (with anchor), second col = burn time
                name_cell = tds[0]
                time_cell = tds[1]
                anchors = name_cell.find_all('a')
                candidate_names = []
                for a in anchors:
                    href = (a.get('href') or '').strip()
                    title = (a.get('title') or '').strip()
                    text = (a.get_text(' ', strip=True) or '').strip()
                    # Skip image/file anchors
                    if href.startswith('/File:') or title.startswith('File:'):
                        continue
                    if text:
                        candidate_names.append(text)
                    elif title:
                        candidate_names.append(title)
                if candidate_names:
                    # Prefer the longest readable candidate (typically the text anchor after the image)
                    nm = max(candidate_names, key=len)
                else:
                    # Fallback to plain text from the cell
                    nm = (name_cell.get_text(' ', strip=True) or '').strip()
                hrs = _parse_hours(time_cell.get_text(' ', strip=True) if time_cell else '') or 1.0
                if nm:
                    results.append({"Name": nm, "Hours": float(hrs)})

    # Fallback: scan generic rows for labels
    if not results:
        for row in soup.find_all('tr'):
            tds = row.find_all('td')
            label_el = row.find('th') or (tds[0] if len(tds) >= 2 else None)
            value_el = (tds[1] if len(tds) >= 2 else (tds[0] if len(tds) == 1 and row.find('th') else None))
            if not label_el or not value_el:
                continue
            label = (label_el.get_text(" ", strip=True) or '').lower()
            if not any(k in label for k in ("consumable", "consumables", "fuel", "lubricant", "upkeep", "maintenance")):
                continue
            anchors = value_el.find_all('a')
            seen = set()
            if anchors:
                for a in anchors:
                    nm = a.get_text(" ", strip=True) or a.get('title') or ''
                    if not nm or nm in seen:
                        continue
                    seen.add(nm)
                    ctx = value_el.get_text(" ", strip=True)
                    hrs = _parse_hours(ctx) or 1.0
                    results.append({"Name": nm, "Hours": float(hrs)})
            else:
                text = value_el.get_text(" ", strip=True)
                parts = [p.strip() for p in re.split(r",|;|\n", text) if p.strip()]
                for p in parts:
                    m = re.match(r"(.+?)\s*(?:x\s*)?(.*)$", p)
                    if not m:
                        continue
                    nm = m.group(1).strip()
                    hrs = _parse_hours(m.group(2)) or 1.0
                    if nm:
                        results.append({"Name": nm, "Hours": float(hrs)})

    # De-dup by Name, keep max hours found
    by_name = {}
    for r in results:
        prev = by_name.get(r["Name"])
        if not prev or r["Hours"] > prev["Hours"]:
            by_name[r["Name"]] = r
    return list(by_name.values())


def scrape_recipe_from_page(url):
    """Scrapes an item page to extract its recipe and power using the 'Build Cost' section first, with debug logs."""
    headers = {'User-Agent': USER_AGENT}
    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        print(f"[page] GET {url} -> HTTP {response.status_code}, bytes={len(response.content)}")
        soup = BeautifulSoup(response.content, 'html.parser')

        recipe = []

        # 1) Preferred: Build Cost section (h2/h3 with span id or text 'Build Cost')
        build_cost_heading = None
        # Try by id first
        for id_candidate in ("Build_Cost", "Build cost", "build_cost"):
            span = soup.find('span', id=id_candidate)
            if span:
                parent_heading = span.find_parent(['h2', 'h3'])
                if parent_heading:
                    build_cost_heading = parent_heading
                    print(f"[parse] Found Build Cost heading via span id='{id_candidate}'.")
                    break
        # Try by heading text
        if build_cost_heading is None:
            for tag in soup.find_all(['h2', 'h3']):
                text = tag.get_text(strip=True).lower()
                if 'build cost' in text:
                    build_cost_heading = tag
                    print("[parse] Found Build Cost heading via text match.")
                    break

        parsed_via = None
        if build_cost_heading is not None:
            # Search siblings until next heading for a list or table
            for sib in _iter_following_until_next_heading(build_cost_heading):
                if sib is None or getattr(sib, 'name', None) is None:
                    continue
                # List-based build cost
                if sib.name in ('ul', 'ol'):
                    parsed_via = 'list'
                    items = sib.find_all('li')
                    print(f"[parse] Build Cost list with {len(items)} items.")
                    for li in items:
                        li_text = li.get_text(" ", strip=True)
                        qty, name = _parse_quantity_and_name(li_text)
                        if not name:
                            # Try anchor text as name if present
                            a = li.find('a')
                            if a:
                                name = a.get_text(strip=True)
                            # Try last resort: remove digits and x
                            if not name:
                                name = re.sub(r"\b\d+\b|x|\(|\)", "", li_text, flags=re.IGNORECASE).strip()
                        if not qty:
                            # Look for trailing/leading number
                            m = re.search(r"(\d+)", li_text)
                            qty = int(m.group(1)) if m else None
                        print(f"[parse]  - li='{li_text}' -> qty={qty}, name='{name}'")
                        if name and qty:
                            recipe.append({"Name": name, "Count": int(qty)})
                    break
                # Table-based build cost
                if sib.name == 'table':
                    parsed_via = 'table'
                    rows = sib.find_all('tr')
                    print(f"[parse] Build Cost table with {len(rows)} rows.")
                    for row in rows:
                        headers = row.find_all('th')
                        cells = row.find_all('td')
                        # Case: header like 'Components' + one TD with the whole list
                        if headers and len(cells) == 1:
                            cell = cells[0]
                            print("[parse]  - header+single-cell row detected; parsing cell content.")
                            # Try LI first
                            li_items = cell.find_all('li')
                            if li_items:
                                print(f"[parse]    contains {len(li_items)} <li> items.")
                                for li in li_items:
                                    li_text = li.get_text(" ", strip=True)
                                    qty, name = _parse_quantity_and_name(li_text)
                                    print(f"[parse]      * li='{li_text}' -> qty={qty}, name='{name}'")
                                    if name and qty:
                                        recipe.append({"Name": name, "Count": int(qty)})
                            else:
                                # No LI; parse anchors and text
                                anchors = cell.find_all('a')
                                seen = set()
                                for a in anchors:
                                    name = a.get_text(strip=True)
                                    if not name:
                                        continue
                                    # Search in the surrounding text for qty
                                    around = a.next_sibling or ''
                                    around_text = around if isinstance(around, str) else getattr(around, 'get_text', lambda *args, **kwargs: '')(" ", strip=True)
                                    m = re.search(r"x\s*(\d+)", around_text, re.IGNORECASE)
                                    qty = int(m.group(1)) if m else None
                                    if qty and name not in seen:
                                        seen.add(name)
                                        print(f"[parse]      * anchor name='{name}' qty={qty}")
                                        recipe.append({"Name": name, "Count": qty})
                                if not anchors:
                                    # Plain text fallback
                                    cell_text = cell.get_text(" ", strip=True)
                                    print(f"[parse]    plain-text scan, len={len(cell_text)}")
                                    pat = re.compile(r"([A-Za-z][A-Za-z0-9 '\-()]+?)\s*[xX]\s*(\d+)")
                                    for m in pat.finditer(cell_text):
                                        name = m.group(1).strip()
                                        qty = int(m.group(2))
                                        if name.lower() in ("components", "requirements", "materials"):
                                            continue
                                        print(f"[parse]      * text-match name='{name}' qty={qty}")
                                        recipe.append({"Name": name, "Count": qty})
                            continue

                        if headers:
                            # Header-only row without data cells; skip
                            continue

                        # Standard two-cell rows
                        if len(cells) >= 2:
                            item_cell, quantity_cell = cells[0], cells[1]
                            link = item_cell.find('a')
                            item_name = link.get_text(strip=True) if link else item_cell.get_text(strip=True)
                            quantity_text = quantity_cell.get_text(strip=True)
                            m = re.search(r"\d+", quantity_text)
                            qty = int(m.group(0)) if m else None
                            print(f"[parse]  - row: name='{item_name}', qty_text='{quantity_text}' -> qty={qty}")
                            if item_name and qty:
                                recipe.append({"Name": item_name, "Count": int(qty)})

                        elif len(cells) == 1:
                            # Single-cell table row; try to parse contents
                            cell = cells[0]
                            # 1) LI items inside the cell
                            li_items = cell.find_all('li')
                            if li_items:
                                print(f"[parse]  - single-cell row contains {len(li_items)} <li> items.")
                                for li in li_items:
                                    li_text = li.get_text(" ", strip=True)
                                    qty, name = _parse_quantity_and_name(li_text)
                                    print(f"[parse]    * li='{li_text}' -> qty={qty}, name='{name}'")
                                    if name and qty:
                                        recipe.append({"Name": name, "Count": int(qty)})
                                continue

                            # 2) Anchor + trailing quantity pattern
                            anchors = cell.find_all('a')
                            if anchors:
                                print(f"[parse]  - single-cell row contains {len(anchors)} anchors; scanning for trailing quantities.")
                                seen = set()
                                for a in anchors:
                                    name = a.get_text(strip=True)
                                    if not name or name in seen:
                                        continue
                                    # Look at immediate text siblings after the anchor
                                    qty = None
                                    for sib2 in a.next_siblings:
                                        if isinstance(sib2, str):
                                            m = re.search(r"x\s*(\d+)", sib2, re.IGNORECASE)
                                            if m:
                                                qty = int(m.group(1))
                                                break
                                        elif getattr(sib2, 'get_text', None):
                                            t = sib2.get_text(" ", strip=True)
                                            m = re.search(r"x\s*(\d+)", t, re.IGNORECASE)
                                            if m:
                                                qty = int(m.group(1))
                                                break
                                    # If not found after, try before
                                    if qty is None:
                                        prev_text = ''
                                        for sib2 in a.previous_siblings:
                                            if isinstance(sib2, str):
                                                prev_text = sib2 + prev_text
                                            elif getattr(sib2, 'get_text', None):
                                                prev_text = sib2.get_text(" ", strip=True) + ' ' + prev_text
                                        m = re.search(r"(\d+)\s*[xX]", prev_text)
                                        if m:
                                            qty = int(m.group(1))

                                    print(f"[parse]    * anchor name='{name}' -> qty={qty}")
                                    if qty:
                                        seen.add(name)
                                        recipe.append({"Name": name, "Count": int(qty)})

                            # 3) Plain text pattern scan inside the cell as a last resort
                            if not recipe:
                                cell_text = cell.get_text(" ", strip=True)
                                print(f"[parse]  - single-cell text fallback, len={len(cell_text)}")
                                # Find all 'Name xNumber' occurrences
                                pat = re.compile(r"([A-Za-z][A-Za-z0-9 '\-()]+?)\s*[xX]\s*(\d+)")
                                for m in pat.finditer(cell_text):
                                    name = m.group(1).strip()
                                    qty = int(m.group(2))
                                    # Skip generic labels
                                    if name.lower() in ("components", "requirements", "materials"):
                                        continue
                                    print(f"[parse]    * text-match name='{name}' qty={qty}")
                                    recipe.append({"Name": name, "Count": qty})
                    break

        # 2) Fallback: legacy 'Recipe' table section
        if not recipe:
            recipe_heading_span = soup.find('span', id='Recipe')
            if recipe_heading_span:
                parsed_via = parsed_via or 'legacy-recipe-table'
                recipe_table = recipe_heading_span.find_parent(['h2', 'h3']).find_next_sibling('table')
                if recipe_table:
                    rows = recipe_table.find_all('tr')
                    print(f"[parse] Legacy Recipe table with {len(rows)} rows.")
                    for row in rows:
                        header_cell = row.find('th')
                        if header_cell and 'Ingredient' in header_cell.get_text():
                            continue
                        cells = row.find_all('td')
                        if len(cells) >= 2:
                            item_cell = cells[0]
                            quantity_cell = cells[1]
                            item_link = item_cell.find('a')
                            item_name = item_link.get_text(strip=True) if item_link else item_cell.get_text(strip=True)
                            quantity = quantity_cell.get_text(strip=True)
                            if item_name:
                                m = re.search(r"\d+", quantity)
                                if m:
                                    qty = int(m.group(0))
                                    print(f"[parse]  - legacy row: name='{item_name}', qty={qty}")
                                    recipe.append({"Name": item_name, "Count": qty})

        if not recipe:
            # Capture a short snippet near Build Cost for debugging
            snippet = None
            if build_cost_heading is not None:
                sib_texts = []
                for i, sib in enumerate(_iter_following_until_next_heading(build_cost_heading)):
                    if i >= 3:
                        break
                    if hasattr(sib, 'get_text'):
                        sib_texts.append(sib.get_text(" ", strip=True))
                snippet = " | ".join(sib_texts)[:240]
            print(f"[parse] WARNING: No recipe parsed for {url}. via={parsed_via or 'n/a'} snippet='{snippet or 'none'}'")

        power = parse_power_from_page(soup)
        water_capacity = parse_water_capacity_from_page(soup)
        consumables = parse_consumables_from_page(soup)
        return {"Recipe": recipe, "Power": power, "WaterCapacity": water_capacity, "Consumables": consumables}

    except requests.exceptions.RequestException as e:
        print(f"[page] ERROR: Could not scrape page {url}: {e}")
        return None


def main():
    """Main function to run the scraper.

    By default, this script will NOT poll external sources.
    Use --update to fetch fresh data from the wiki; otherwise we merge manual items
    into the existing items_data.json if present and write the result out.
    """
    parser = argparse.ArgumentParser(description="Dune: Awakening data scraper")
    parser.add_argument("--update", "-u", action="store_true", help="Fetch fresh data from external sources (wiki)")
    args = parser.parse_args()

    try:
        all_item_data = []
        if args.update:
            print("[mode] Update mode ON: polling external sources (wiki)...")
            item_links = get_placeable_links(CATEGORY_URL)
            for i, link in enumerate(item_links):
                print(f"[item] Processing {i+1}/{len(item_links)}: {link}")
                result = scrape_recipe_from_page(link)
                name = unquote(link.split('/')[-1]).replace('_', ' ').strip()
                if result is not None:
                    item_data = {
                        "Name": name,
                        "Recipe": result.get("Recipe", []),
                        "Power": result.get("Power", {"Provides": 0, "Consumes": 0}),
                        "WaterCapacity": int(result.get("WaterCapacity", 0)),
                        "Consumables": result.get("Consumables", [])
                    }
                    all_item_data.append(item_data)
                time.sleep(1)  # respectful delay
        else:
            print("[mode] Update mode OFF: skipping external polling.")
            if os.path.exists(OUTPUT_FILENAME):
                try:
                    with open(OUTPUT_FILENAME, 'r', encoding='utf-8') as f:
                        all_item_data = json.load(f)
                    print(f"[load] Loaded {len(all_item_data)} items from existing {OUTPUT_FILENAME}.")
                except Exception as e:
                    print(f"[load] WARNING: Could not load existing {OUTPUT_FILENAME}: {e}")
                    all_item_data = []
            else:
                print(f"[load] No existing {OUTPUT_FILENAME} found; starting with empty set.")

        # Merge manual items regardless of mode
        all_item_data = merge_manual_items(all_item_data)

        # Ensure minimal "consumable" items exist for selection (no recipe needed)
        existing_names = {i.get("Name") for i in all_item_data}
        minimal_consumables = []
        for item in list(all_item_data):
            for c in (item.get("Consumables") or []):
                cname = c.get("Name")
                if cname and cname not in existing_names:
                    minimal_consumables.append({
                        "Name": cname,
                        "Recipe": [],
                        "Power": {"Provides": 0, "Consumes": 0},
                        "WaterCapacity": 0,
                        "IsConsumable": True
                    })
                    existing_names.add(cname)
        all_item_data.extend(minimal_consumables)

        print(f"\n[save] Saving data for {len(all_item_data)} items to {OUTPUT_FILENAME}...")
        with open(OUTPUT_FILENAME, 'w', encoding='utf-8') as f:
            json.dump(all_item_data, f, indent=4)
        try:
            size = os.path.getsize(OUTPUT_FILENAME)
            print(f"[save] Wrote {size} bytes to {OUTPUT_FILENAME}.")
        except Exception as e:
            print(f"[save] NOTE: Could not stat output file: {e}")
        print("[done] Completed.")

    except Exception as e:
        print(f"\n[fatal] An unexpected error occurred: {e}")

if __name__ == "__main__":
    main()
