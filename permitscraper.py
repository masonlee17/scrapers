#!/usr/bin/env python3
"""
LADBS Permit Scraper

Scrapes permit information from the Los Angeles Department of Building and Safety (LADBS) website.
"""

import argparse
import csv
import json
import re
import sys
import time
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

BASE = "https://www.ladbsservices2.lacity.org/OnlineServices"

@dataclass
class ParcelProfile:
    address_id: str
    pin: Optional[str] = None
    zoning: Optional[str] = None
    council_district: Optional[str] = None
    community_plan_area: Optional[str] = None

@dataclass
class PermitRecord:
    address_id: str
    permit_url: str
    permit_number: Optional[str] = None
    permit_type: Optional[str] = None
    status: Optional[str] = None
    issued_date: Optional[str] = None
    finaled_date: Optional[str] = None
    expiration_date: Optional[str] = None
    valuation: Optional[str] = None
    description: Optional[str] = None
    job_address: Optional[str] = None
    contractor: Optional[str] = None
    applicant: Optional[str] = None

def clean_text(s: Optional[str]) -> Optional[str]:
    if not s:
        return s
    return re.sub(r"\s+", " ", s).strip()

def request_get(url: str, params: Dict = None, timeout: int = 30, retries: int = 2) -> requests.Response:
    last_err = None
    for i in range(retries + 1):
        try:
            resp = requests.get(url, params=params, timeout=timeout)
            resp.raise_for_status()
            return resp
        except Exception as e:
            last_err = e
            time.sleep(1.0 + i * 0.5)
    raise last_err

def fetch_parcel_profile(address_id: str) -> ParcelProfile:
    url = f"{BASE}/ParcelProfileDetail/{address_id}"
    resp = request_get(url)
    soup = BeautifulSoup(resp.text, "lxml")

    def find_value_by_label(label_texts: List[str]) -> Optional[str]:
        text = soup.get_text(" ", strip=True)
        for lbl in label_texts:
            m = re.search(re.escape(lbl) + r"\s*[:：]?\s*(.+?)\s{2,}", text)
            if m:
                return clean_text(m.group(1))
        return None

    pin = find_value_by_label(["Parcel Identification Number"])
    zoning = find_value_by_label(["Zone(s)"])
    council = find_value_by_label(["Council District"])
    cpa = find_value_by_label(["Community Plan Area"])

    return ParcelProfile(
        address_id=address_id,
        pin=pin,
        zoning=zoning,
        council_district=council,
        community_plan_area=cpa,
    )

def collect_permit_data_with_playwright(address_id: str, headless: bool = True) -> List[PermitRecord]:
    """Collect permit data directly from the results page using Playwright"""
    target = f"{BASE}/PermitReport/PermitResults/{address_id}"
    permits = []
    print(f"[info] Navigating to: {target}")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx = browser.new_context()
        page = ctx.new_page()
        
        try:
            # Load the page
            print(f"[info] Loading page...")
            page.goto(target, wait_until="networkidle", timeout=60000)
            
            # Wait a bit for any dynamic content
            page.wait_for_timeout(3000)
            
            # Save debug HTML for analysis
            debug_file = f"debug_page_{address_id}.html"
            with open(debug_file, "w", encoding="utf-8") as f:
                f.write(page.content())
            print(f"[info] Saved debug HTML to {debug_file}")
            
            # First, find and click the "Permit Information found:" section
            print(f"[info] Looking for 'Permit Information found:' section...")
            permit_section = None
            
            # Look for the specific text pattern from the web search results
            permit_section = page.query_selector('h3:has-text("Permit Information found:")')
            if permit_section:
                print(f"[info] Found permit section! Clicking...")
                permit_section.click()
                
                # Wait for network requests to complete
                print(f"[info] Waiting for network requests...")
                page.wait_for_load_state("networkidle", timeout=10000)
                page.wait_for_timeout(3000)
            else:
                # Fallback: look through all h3 elements
                h3_elements = page.query_selector_all('h3')
                for i, h3 in enumerate(h3_elements):
                    text = h3.inner_text().strip()
                    if "Permit Information found:" in text:
                        permit_section = h3
                        print(f"[info] Found permit section! Clicking...")
                        h3.click()
                        
                        # Wait for network requests to complete
                        print(f"[info] Waiting for network requests...")
                        page.wait_for_load_state("networkidle", timeout=10000)
                        page.wait_for_timeout(3000)
                        break
            
            if not permit_section:
                print(f"[warn] Could not find 'Permit Information found:' section")
                return permits
            
            # Look for individual permit dropdowns that appear after clicking the main section
            # These are typically h3 elements with onclick attributes or specific classes
            permit_dropdowns = page.query_selector_all('h3[onclick]')
            print(f"[info] Found {len(permit_dropdowns)} permit dropdown sections")
            
            # Also try other selectors for permit dropdowns
            if not permit_dropdowns:
                permit_dropdowns = page.query_selector_all('h3[id*="PermitSummary"]')
                print(f"[info] Found {len(permit_dropdowns)} permit summary sections")
            
            if not permit_dropdowns:
                permit_dropdowns = page.query_selector_all('h3[class*="accordion"]')
                print(f"[info] Found {len(permit_dropdowns)} accordion sections")
            
            # Click each permit dropdown to expand them
            for i, section in enumerate(permit_dropdowns):
                try:
                    section_text = section.inner_text().strip()
                    section_id = section.get_attribute('id') or 'no-id'
                    print(f"[info] Clicking permit dropdown {i+1}: {section_text[:50]}... (id: {section_id})")
                    section.click()
                    page.wait_for_timeout(2000)  # Wait longer for content to load
                except Exception as e:
                    print(f"[warn] Could not click permit dropdown {i+1}: {e}")
            
            # Wait for all sections to load
            page.wait_for_timeout(3000)
            
            # Look for permit data in tables
            print(f"[info] Looking for permit data...")
            tables = page.query_selector_all('table')
            print(f"[info] Found {len(tables)} tables")
            
            for i, table in enumerate(tables):
                rows = table.query_selector_all('tr')
                if len(rows) >= 2:  # Has header and data
                    headers = [cell.inner_text().strip() for cell in rows[0].query_selector_all('td, th')]
                    print(f"[info] Table {i+1} headers: {headers}")
                    
                    # Check if this looks like a permit table - be more flexible with the check
                    is_permit_table = False
                    for h in headers:
                        h_lower = h.lower()
                        if any(keyword in h_lower for keyword in ['permit', 'application', 'type', 'status', 'issued', 'finaled', 'contractor', 'applicant']):
                            is_permit_table = True
                            break
                    
                    if is_permit_table:
                        print(f"[info] Found permit table! Processing {len(rows)-1} data rows...")
                        
                        # Process all data rows
                        for j, row in enumerate(rows[1:], 1):
                            cells = [cell.inner_text().strip() for cell in row.query_selector_all('td, th')]
                            if cells and any(cells):  # Skip empty rows
                                print(f"[info] Processing row {j}: {cells}")
                                
                                # Create permit record
                                permit_data = {}
                                for k, header in enumerate(headers):
                                    if k < len(cells):
                                        permit_data[header] = cells[k]
                                
                                # Look for permit detail link in this row
                                permit_url = ""
                                try:
                                    links = row.query_selector_all('a[href*="PcisPermitDetail"]')
                                    if links:
                                        href = links[0].get_attribute("href") or ""
                                        if href:
                                            if href.startswith("http"):
                                                permit_url = href
                                            else:
                                                permit_url = urljoin(BASE + "/", href.lstrip("/"))
                                except:
                                    pass
                                
                                # Map to our standard fields - try different possible header names
                                permit = PermitRecord(
                                    address_id=address_id,
                                    permit_url=permit_url,
                                    permit_number=permit_data.get('Application/Permit #') or permit_data.get('Permit #') or permit_data.get('Application #'),
                                    permit_type=permit_data.get('Type') or permit_data.get('Permit Type'),
                                    status=permit_data.get('Status') or permit_data.get('Permit Status'),
                                    description=permit_data.get('Work Description') or permit_data.get('Description') or permit_data.get('Job Description'),
                                    job_address=permit_data.get('Job Address') or permit_data.get('Address'),
                                    contractor=permit_data.get('Contractor') or permit_data.get('Contractor Name'),
                                    applicant=permit_data.get('Applicant') or permit_data.get('Applicant Name'),
                                    issued_date=permit_data.get('Issued Date') or permit_data.get('Issue Date'),
                                    finaled_date=permit_data.get('Finaled Date') or permit_data.get('Final Date'),
                                    expiration_date=permit_data.get('Expiration Date') or permit_data.get('Exp Date'),
                                    valuation=permit_data.get('Job Valuation') or permit_data.get('Valuation') or permit_data.get('Value')
                                )
                                
                                permits.append(permit)
                                print(f"[info] Added permit: {permit.permit_number} - {permit.permit_type}")
                    else:
                        print(f"[info] Table {i+1} is not a permit table (headers: {headers})")
            
            print(f"[info] Found {len(permits)} permits total")
            
        except Exception as e:
            print(f"[error] Error during scraping: {e}")
        finally:
            browser.close()
    
    return permits

def run(address_id: str, headless: bool = True) -> Tuple[ParcelProfile, List[PermitRecord]]:
    errors = []
    
    # 1) Parcel Profile (server-rendered)
    try:
        profile = fetch_parcel_profile(address_id)
        print(f"[info] Successfully fetched parcel profile")
    except Exception as e:
        print(f"[error] Failed to fetch parcel profile: {e}")
        errors.append(f"Parcel profile: {e}")
        profile = ParcelProfile(address_id=address_id)
    
    # 2) Permits (JS-rendered, needs Playwright)
    try:
        permits = collect_permit_data_with_playwright(address_id, headless=headless)
        print(f"[info] Successfully collected {len(permits)} permits")
    except Exception as e:
        print(f"[error] Failed to collect permits: {e}")
        errors.append(f"Permits: {e}")
        permits = []
    
    # Report any errors
    if errors:
        print(f"[warn] Encountered {len(errors)} errors:")
        for error in errors:
            print(f"[warn]   - {error}")
    
    return profile, permits

def write_outputs(address_id: str, profile: ParcelProfile, permits: List[PermitRecord]) -> None:
    # JSON
    out_json = {
        "address_id": address_id,
        "parcel_profile": asdict(profile),
        "permits": [asdict(p) for p in permits],
        "source": {
            "results_url": f"{BASE}/PermitReport/PermitResults/{address_id}",
            "parcel_profile_url": f"{BASE}/ParcelProfileDetail/{address_id}",
        },
    }
    json_path = f"ladbs_{address_id}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(out_json, f, ensure_ascii=False, indent=2)
    print(f"[info] Wrote {json_path}")

    # CSV
    csv_path = f"ladbs_{address_id}.csv"
    fieldnames = [f for f in PermitRecord.__dataclass_fields__.keys()]
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for p in permits:
            w.writerow(asdict(p))
    print(f"[info] Wrote {csv_path}")

def main():
    ap = argparse.ArgumentParser(description="LADBS permit scraper")
    ap.add_argument("--address-id", type=str, required=True, help="LADBS address ID")
    ap.add_argument("--headed", action="store_true", help="Show the browser")
    args = ap.parse_args()

    address_id = args.address_id
    headless = not args.headed
    
    print(f"[info] Starting LADBS permit scraper for address ID: {address_id}")
    
    # Run the scraper
    profile, permits = run(address_id, headless=headless)
    
    # Write outputs
    write_outputs(address_id, profile, permits)
    
    print(f"[success] Done! Found {len(permits)} permits for address ID {address_id}")

if __name__ == "__main__":
    main()
