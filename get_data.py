import requests
from bs4 import BeautifulSoup
import json
import time
import re
from urllib.parse import unquote

# Configuration
BASE_URL = "https://awakening.wiki"
CATEGORY_URL = f"{BASE_URL}/Category:Placeables"
OUTPUT_FILENAME = "items_data.json"
USER_AGENT = "DuneAwakeningDataExtractor/4.0 (Python script; github.com/Gizmo3030/Dune-Awakening-API)"

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


def scrape_recipe_from_page(url):
    """Scrapes an item page to extract its recipe using the 'Build Cost' section first, with debug logs."""
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

        return recipe
        
    except requests.exceptions.RequestException as e:
        print(f"[page] ERROR: Could not scrape page {url}: {e}")
        return None


def main():
    """Main function to run the scraper."""
    try:
        item_links = get_placeable_links(CATEGORY_URL)
        all_item_data = []

        for i, link in enumerate(item_links):
            print(f"[item] Processing {i+1}/{len(item_links)}: {link}")
            
            recipe = scrape_recipe_from_page(link)
            
            # Get item name from the end of the link
            name = unquote(link.split('/')[-1]).replace('_', ' ').strip()
            
            if recipe is not None:
                item_data = {
                    "Name": name,
                    "Recipe": recipe
                }
                all_item_data.append(item_data)
            
            # Respectful delay
            time.sleep(1)

        print(f"\n[save] Saving data for {len(all_item_data)} items to {OUTPUT_FILENAME}...")
        with open(OUTPUT_FILENAME, 'w', encoding='utf-8') as f:
            json.dump(all_item_data, f, indent=4)
        # Report file size
        try:
            import os
            size = os.path.getsize(OUTPUT_FILENAME)
            print(f"[save] Wrote {size} bytes to {OUTPUT_FILENAME}.")
        except Exception as e:
            print(f"[save] NOTE: Could not stat output file: {e}")
        print("[done] Completed.")

    except Exception as e:
        print(f"\n[fatal] An unexpected error occurred: {e}")

if __name__ == "__main__":
    main()
