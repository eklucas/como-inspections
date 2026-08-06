#!/usr/bin/env python3
"""Scrape today's inspections from Columbia, MO EnerGov portal."""

import csv
import os
import sys
import tempfile
from datetime import date
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

BASE_URL = "https://energov.como.gov/EnerGov_Prod/SelfService"
PAGE_URL = f"{BASE_URL}#/inspection/todaysinspections"
OUTPUT_DIR = Path("data")
TABLE_SELECTOR = "#selfServiceTable-TodaysInspections tbody tr"
INSPECTIONS_API = "api/energov/inspections/todaysinspections"


def scrape():
    today = date.today().isoformat()

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()

        print(f"Loading {PAGE_URL}")
        try:
            with page.expect_response(
                lambda r: INSPECTIONS_API in r.url and r.request.method == "POST",
                timeout=120_000,
            ) as response_info:
                page.goto(PAGE_URL, wait_until="domcontentloaded", timeout=120_000)
            total_found = response_info.value.json().get("TotalFound", 0)
        except PlaywrightTimeoutError:
            print("ERROR: Inspections data did not load within 120 seconds.", file=sys.stderr)
            sys.exit(1)

        if not total_found:
            print("No inspections today — exiting without writing file.")
            browser.close()
            return []

        # The grid has data at this point (confirmed via the API response
        # above), so this just waits for Angular to render it — no race
        # against the "No records to display" placeholder.
        page.wait_for_selector(TABLE_SELECTOR, timeout=60_000)

        # Open export dialog
        page.locator("button", has_text="Export").click()
        page.wait_for_selector("#filename", timeout=60_000)

        page.locator("#filename").fill("export")
        page.locator("input[type='radio']").first.check()

        with page.expect_download(timeout=60_000) as dl:
            page.get_by_role("button", name="Ok").click()

        download = dl.value
        temp_path = Path(tempfile.mktemp(suffix=".csv"))
        download.save_as(temp_path)

        browser.close()

    rows = []
    with open(temp_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            row["scrape_date"] = today
            rows.append(row)

    temp_path.unlink(missing_ok=True)
    return rows


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)

    rows = scrape()
    print(f"Scraped {len(rows)} rows.")

    if not rows:
        print("No data found — exiting without writing file.")
        sys.exit(0)

    today = date.today().isoformat()
    output_path = OUTPUT_DIR / f"inspections_{today}.csv"

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    print(f"Saved {len(rows)} rows to {output_path}")

    # if os.environ.get("R2_BUCKET"):
    #     from upload import upload
    #     upload(output_path, f"inspections/inspections_{today}.csv")


if __name__ == "__main__":
    main()
