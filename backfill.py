#!/usr/bin/env python3
"""One-time backfill: scrape inspections for each date from April 8, 2025 to yesterday."""

import csv
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

BASE_URL = "https://energov.como.gov/EnerGov_Prod/SelfService"
PAGE_URL = f"{BASE_URL}#/inspection/todaysinspections"
OUTPUT_DIR = Path("data")
TABLE_SELECTOR = "#selfServiceTable-TodaysInspections tbody tr"

START_DATE = date(2025, 4, 8)
END_DATE = date.today() - timedelta(days=1)  # yesterday

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


def set_date(page, target_date):
    """Type a date into the ScheduledDate input and wait for the table to reload.

    The site's date picker interprets input as UTC midnight, which rolls back
    one day in Central Time. We pass target_date + 1 day so the website lands
    on the correct date.
    """
    picker_date = target_date + timedelta(days=1)
    date_str = picker_date.strftime("%m/%d/%Y")

    date_input = page.locator("#ScheduledDate")
    date_input.click(click_count=3)
    date_input.type(date_str)
    date_input.press("Tab")

    # Wait for AngularJS to react and any network requests to settle
    page.wait_for_load_state("networkidle", timeout=20_000)


def scrape_rows_for_page(page, target_date):
    """Scrape all paginated rows for the current date view."""
    rows = []
    scrape_date_str = target_date.isoformat()
    page_num = 1

    while True:
        table_rows = extract_rows(page)
        if not table_rows:
            break

        for r in table_rows:
            href = r[0] if len(r) > 0 else ""
            link_text = r[1] if len(r) > 1 else ""
            full_url = f"{BASE_URL}{href}" if href.startswith("#") else href

            cells = r[2:]
            rows.append({
                "inspection_id": link_text.strip(),
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
                "scrape_date": scrape_date_str,
            })

        next_a = page.locator('a[aria-label="next page"]')
        if next_a.count() == 0:
            break
        # Use evaluate() to read the live DOM className — AngularJS applies
        # "disabled" via ng-class, which updates the property not the attribute
        li_class = next_a.evaluate('el => el.parentElement.className')
        if "disabled" in li_class:
            break

        # Pass first-row ID as a JS argument (not string-interpolated) so
        # special characters in the text can't break the JS expression
        first_id = table_rows[0][1]
        next_a.click()
        page.wait_for_load_state("networkidle", timeout=15_000)
        page.wait_for_function(
            """(prevId) => {
                const tr = document.querySelector('#selfServiceTable-TodaysInspections tbody tr');
                if (!tr) return false;
                const a = tr.querySelector('a');
                return a && a.textContent.trim() !== prevId;
            }""",
            arg=first_id,
            timeout=15_000,
        )
        page_num += 1

    return rows


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)

    dates = []
    d = START_DATE
    while d <= END_DATE:
        dates.append(d)
        d += timedelta(days=1)

    print(f"Backfilling {len(dates)} dates: {START_DATE} to {END_DATE}")

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()

        print(f"Loading {PAGE_URL}")
        page.goto(PAGE_URL, wait_until="networkidle", timeout=60_000)

        try:
            page.wait_for_selector(TABLE_SELECTOR, timeout=30_000)
        except PlaywrightTimeoutError:
            print("ERROR: Table did not load.", file=sys.stderr)
            sys.exit(1)

        # Set page size to 100 once — persists across date changes
        try:
            page.locator("#pageSizeList").select_option("100")
            page.wait_for_load_state("networkidle", timeout=60_000)
        except Exception as e:
            print(f"Warning: could not set page size: {e}", file=sys.stderr)

        for i, target_date in enumerate(dates):
            output_path = OUTPUT_DIR / f"inspections_{target_date.isoformat()}.csv"

            if output_path.exists():
                print(f"[{i+1}/{len(dates)}] {target_date} — already exists, skipping")
                continue

            print(f"[{i+1}/{len(dates)}] {target_date} ...", end=" ", flush=True)

            try:
                set_date(page, target_date)
                rows = scrape_rows_for_page(page, target_date)
            except PlaywrightTimeoutError:
                print(f"TIMEOUT — skipping")
                continue
            except Exception as e:
                print(f"ERROR: {e} — skipping")
                continue

            if rows:
                with open(output_path, "w", newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=HEADERS)
                    writer.writeheader()
                    writer.writerows(rows)
                print(f"{len(rows)} rows saved")
            else:
                print(f"no records")
                # Write an empty file so we don't re-scrape this date
                with open(output_path, "w", newline="", encoding="utf-8") as f:
                    csv.DictWriter(f, fieldnames=HEADERS).writeheader()

            # Be polite — small delay between requests
            time.sleep(1)

        browser.close()

    print("Done.")


if __name__ == "__main__":
    main()
