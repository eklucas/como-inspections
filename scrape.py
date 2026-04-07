#!/usr/bin/env python3
"""Scrape today's inspections from Columbia, MO EnerGov portal."""

import csv
import sys
from datetime import date
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

BASE_URL = "https://energov.como.gov/EnerGov_Prod/SelfService"
PAGE_URL = f"{BASE_URL}#/inspection/todaysinspections"
OUTPUT_DIR = Path("data")

HEADERS = [
    "inspection_id",
    "inspection_url",
    "case_number",
    "case_type",
    "inspection_type",
    "address",
    "primary_inspector",
    "estimated_start_time",
    "estimated_end_time",
    "status",
    "order",
    "scrape_date",
]


TABLE_SELECTOR = "#selfServiceTable-TodaysInspections tbody tr"


def extract_rows(page):
    """Extract all data rows from the currently visible table page."""
    return page.evaluate("""() => {
        const rows = [];
        const table = document.getElementById('selfServiceTable-TodaysInspections');
        if (!table) return rows;

        for (const tr of table.querySelectorAll('tbody tr')) {
            const cells = tr.querySelectorAll('td');
            if (cells.length === 0) continue;

            const linkEl = cells[0].querySelector('a[href]');
            const href = linkEl ? linkEl.getAttribute('href') : '';
            const linkText = linkEl ? linkEl.textContent.trim() : cells[0].textContent.trim();

            rows.push([href, linkText, ...Array.from(cells).slice(1).map(td => td.textContent.trim())]);
        }
        return rows;
    }""")


def scrape():
    all_rows = []
    today = date.today().isoformat()

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()

        print(f"Loading {PAGE_URL}")
        page.goto(PAGE_URL, wait_until="networkidle", timeout=60_000)

        try:
            page.wait_for_selector(TABLE_SELECTOR, timeout=30_000)
        except PlaywrightTimeoutError:
            print("ERROR: Table did not load within 30 seconds.", file=sys.stderr)
            sys.exit(1)

        # Set results per page to 100 to reduce pagination
        try:
            page.locator("#pageSizeList").select_option("100")
            page.wait_for_load_state("networkidle", timeout=15_000)
            page.wait_for_selector(TABLE_SELECTOR, timeout=15_000)
        except Exception as e:
            print(f"Warning: could not set page size: {e}", file=sys.stderr)

        page_num = 1
        while True:
            print(f"  Scraping page {page_num}...")
            table_rows = extract_rows(page)

            for r in table_rows:
                href = r[0] if len(r) > 0 else ""
                link_text = r[1] if len(r) > 1 else ""
                inspection_id = link_text.strip()
                full_url = f"{BASE_URL}{href}" if href.startswith("#") else href

                cells = r[2:]
                all_rows.append({
                    "inspection_id": inspection_id,
                    "inspection_url": full_url,
                    "case_number": cells[0] if len(cells) > 0 else "",
                    "case_type": cells[1] if len(cells) > 1 else "",
                    "inspection_type": cells[2] if len(cells) > 2 else "",
                    "address": cells[3] if len(cells) > 3 else "",
                    "primary_inspector": cells[4] if len(cells) > 4 else "",
                    "estimated_start_time": cells[5] if len(cells) > 5 else "",
                    "estimated_end_time": cells[6] if len(cells) > 6 else "",
                    "status": cells[7] if len(cells) > 7 else "",
                    "order": cells[8] if len(cells) > 8 else "",
                    "scrape_date": today,
                })

            # Check for a next-page link that isn't disabled
            next_li = page.locator('li:has(a[aria-label="next page"])')
            if next_li.count() == 0:
                break
            li_class = next_li.get_attribute("class") or ""
            if "disabled" in li_class or "ng-hide" in li_class:
                break

            next_li.locator("a").click()
            page.wait_for_load_state("networkidle", timeout=15_000)
            page.wait_for_selector(TABLE_SELECTOR, timeout=15_000)
            page_num += 1

        browser.close()

    return all_rows


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)

    rows = scrape()
    print(f"Scraped {len(rows)} rows.")

    if not rows:
        print("No data found — exiting without writing file.", file=sys.stderr)
        sys.exit(1)

    today = date.today().isoformat()
    output_path = OUTPUT_DIR / f"inspections_{today}.csv"

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=HEADERS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Saved {len(rows)} rows to {output_path}")


if __name__ == "__main__":
    main()
