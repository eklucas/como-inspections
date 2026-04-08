#!/usr/bin/env python3
"""One-time backfill: scrape inspections for each date from April 8, 2025 to yesterday."""

import csv
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

BASE_URL = "https://energov.como.gov/EnerGov_Prod/SelfService"
PAGE_URL = f"{BASE_URL}#/inspection/todaysinspections"
OUTPUT_DIR = Path("data")
TABLE_SELECTOR = "#selfServiceTable-TodaysInspections tbody tr"

START_DATE = date(2025, 4, 8)
# END_DATE = date.today() - timedelta(days=1)  # full backfill
END_DATE = date(2025, 4, 11)  # testing


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

    page.wait_for_load_state("networkidle", timeout=20_000)


def export_date(page, target_date):
    """Click the Export button to download all rows for the current date view.

    Returns a list of dicts with scrape_date added. On the first call, prints
    the raw export column headers so you can see exactly what's included.
    """
    # Open the export dialog
    page.locator('button', has_text="Export").click()
    page.wait_for_selector("#filename", timeout=10_000)

    # Give the dialog a unique filename (content doesn't matter)
    page.locator("#filename").fill("backfill_export")

    # Ensure "Export first 1000 Results" is selected (it's the default, but be explicit)
    page.locator('input[type="radio"]').first.check()

    # Capture the download before clicking OK
    with page.expect_download(timeout=30_000) as dl:
        page.get_by_role("button", name="Ok").click()

    download = dl.value

    # Save to a temp file and parse
    temp_path = Path(tempfile.mktemp(suffix=".csv"))
    download.save_as(temp_path)

    rows = []
    headers_printed = getattr(export_date, "_headers_printed", False)

    with open(temp_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not headers_printed:
            print(f"\n  Export columns: {reader.fieldnames}")
            export_date._headers_printed = True
        for row in reader:
            row["scrape_date"] = target_date.isoformat()
            rows.append(row)

    temp_path.unlink(missing_ok=True)
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

        for i, target_date in enumerate(dates):
            output_path = OUTPUT_DIR / f"inspections_{target_date.isoformat()}.csv"

            if output_path.exists():
                print(f"[{i+1}/{len(dates)}] {target_date} — already exists, skipping")
                continue

            print(f"[{i+1}/{len(dates)}] {target_date} ...", end=" ", flush=True)

            try:
                set_date(page, target_date)
                rows = export_date(page, target_date)
            except PlaywrightTimeoutError:
                print("TIMEOUT — skipping")
                continue
            except Exception as e:
                print(f"ERROR: {e} — skipping")
                continue

            if rows:
                output_path.write_text("")  # create file before writing
                with open(output_path, "w", newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=rows[0].keys())
                    writer.writeheader()
                    writer.writerows(rows)
                print(f"{len(rows)} rows saved")
            else:
                print("no records")
                output_path.write_text("scrape_date\n")  # empty sentinel file

            time.sleep(1)

        browser.close()

    print("Done.")


if __name__ == "__main__":
    main()
