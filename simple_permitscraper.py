#!/usr/bin/env python3
"""
Simple LADBS Permit Scraper - Working Version

This is a simplified version that focuses on what actually works.
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

BASE = "https://www.ladbsservices2.lacity.org/OnlineServices/PermitReport"

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

def scrape_permits_simple(address_id: str, headless: bool = True) -> List[PermitRecord]:
    """Simple scraper that focuses on what works"""
    target = f"{BASE}/PermitResults/{address_id}"
    permits = []
    
    print(f"[info] Scraping permits for address ID: {address_id}")
    print(f"[info] URL: {target}")
    
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
            
            # First, find and click the "Permit Information found:" section
            print(f"[info] Looking for 'Permit Information found:' section...")
            permit_section = None
            h3_elements = page.query_selector_all('h3')
            
            for i, h3 in enumerate(h3_elements):
                text = h3.inner_text().strip()
                print(f"[info] h3 {i+1}: {text[:50]}...")
                if "Permit Information found:" in text:
                    permit_section = h3
                    print(f"[info] Found permit section! Clicking...")
                    h3.click()
                    
                    # Wait for network requests to complete
                    print(f"[info] Waiting for network requests...")
                    page.wait_for_load_state("networkidle", timeout=10000)
                    page.wait_for_timeout(3000)  # Additional wait
                    break
            
            if not permit_section:
                print(f"[warn] Could not find 'Permit Information found:' section")
                # Try clicking all h3 elements as fallback
                for i, h3 in enumerate(h3_elements):
                    try:
                        text = h3.inner_text().strip()
                        print(f"[info] Clicking h3 {i+1}: {text[:50]}...")
                        h3.click()
                        page.wait_for_timeout(1000)
                    except Exception as e:
                        print(f"[warn] Could not click h3 {i+1}: {e}")
            
            # Wait for content to load
            print(f"[info] Waiting for content to load...")
            page.wait_for_timeout(3000)
            
            # Save HTML for debugging
            html_content = page.content()
            with open(f"debug_page_{address_id}.html", "w", encoding="utf-8") as f:
                f.write(html_content)
            print(f"[info] Saved page HTML to debug_page_{address_id}.html")
            
            # Check if we can see permit data in the page content
            if "Application/Permit #" in html_content:
                print(f"[info] Found 'Application/Permit #' in page content!")
            else:
                print(f"[info] 'Application/Permit #' not found in page content")
            
            # Look for individual permit summary sections that need to be clicked
            permit_summary_sections = page.query_selector_all('h3[id*="PermitSummary"]')
            print(f"[info] Found {len(permit_summary_sections)} permit summary sections")
            
            # Click each permit summary section to expand them
            for i, section in enumerate(permit_summary_sections):
                try:
                    section_id = section.get_attribute('id')
                    print(f"[info] Clicking permit summary section {i+1}: {section_id}")
                    section.click()
                    page.wait_for_timeout(1500)  # Wait for each section to load
                except Exception as e:
                    print(f"[warn] Could not click permit summary section {i+1}: {e}")
            
            # Wait for all sections to load
            page.wait_for_timeout(2000)
            
            # Look for permit data in tables and dropdowns
            print(f"[info] Looking for permit data...")
            tables = page.query_selector_all('table')
            print(f"[info] Found {len(tables)} tables")
            
            # Look for dropdowns or divs that might contain permit data
            permit_containers = page.query_selector_all('div, section, details')
            print(f"[info] Found {len(permit_containers)} potential containers")
            
            # Look for any elements containing permit numbers or Application/Permit
            permit_elements = page.query_selector_all('*:has-text("Application/Permit")')
            print(f"[info] Found {len(permit_elements)} elements containing 'Application/Permit'")
            
            # Look for any elements containing permit numbers (like 20010)
            number_elements = page.query_selector_all('*:has-text("20010")')
            print(f"[info] Found {len(number_elements)} elements containing '20010'")
            
            # Process tables
            for i, table in enumerate(tables):
                rows = table.query_selector_all('tr')
                if len(rows) >= 2:  # Has header and data
                    headers = [cell.inner_text().strip() for cell in rows[0].query_selector_all('td, th')]
                    print(f"[info] Table {i+1} headers: {headers}")
                    
                    # Check if this looks like a permit table
                    if any('permit' in h.lower() or 'application' in h.lower() for h in headers):
                        print(f"[info] Found permit table!")
                        
                        # Process all data rows
                        for j, row in enumerate(rows[1:], 1):
                            cells = [cell.inner_text().strip() for cell in row.query_selector_all('td, th')]
                            if cells and any(cells):  # Skip empty rows
                                print(f"[info] Processing table row {j}: {cells}")
                                
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
                                
                                # Map to our standard fields
                                permit = PermitRecord(
                                    address_id=address_id,
                                    permit_url=permit_url,
                                    permit_number=permit_data.get('Application/Permit #'),
                                    permit_type=permit_data.get('Type'),
                                    status=permit_data.get('Status'),
                                    description=permit_data.get('Work Description'),
                                    job_address=permit_data.get('Job Address'),
                                    contractor=permit_data.get('Contractor'),
                                    applicant=permit_data.get('Applicant'),
                                    issued_date=permit_data.get('Issued Date'),
                                    finaled_date=permit_data.get('Finaled Date'),
                                    expiration_date=permit_data.get('Expiration Date'),
                                    valuation=permit_data.get('Job Valuation')
                                )
                                
                                permits.append(permit)
                                print(f"[info] Added permit: {permit.permit_number} - {permit.permit_type}")
            
            # Also look for permit data in other elements (dropdowns, divs, etc.)
            print(f"[info] Looking for permit data in other elements...")
            for i, element in enumerate(permit_containers[:10]):  # Check first 10 containers
                try:
                    text = element.inner_text().strip()
                    if text and ('Application/Permit' in text or 'permit' in text.lower()):
                        print(f"[info] Container {i+1} contains permit text: {text[:100]}...")
                        
                        # Look for tables within this container
                        inner_tables = element.query_selector_all('table')
                        for j, inner_table in enumerate(inner_tables):
                            rows = inner_table.query_selector_all('tr')
                            if len(rows) >= 2:
                                headers = [cell.inner_text().strip() for cell in rows[0].query_selector_all('td, th')]
                                print(f"[info] Inner table {j+1} headers: {headers}")
                                
                                # Process rows
                                for k, row in enumerate(rows[1:], 1):
                                    cells = [cell.inner_text().strip() for cell in row.query_selector_all('td, th')]
                                    if cells and any(cells):
                                        print(f"[info] Processing inner table row {k}: {cells}")
                                        
                                        # Create permit record
                                        permit_data = {}
                                        for l, header in enumerate(headers):
                                            if l < len(cells):
                                                permit_data[header] = cells[l]
                                        
                                        # Look for permit detail link
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
                                        
                                        # Map to our standard fields
                                        permit = PermitRecord(
                                            address_id=address_id,
                                            permit_url=permit_url,
                                            permit_number=permit_data.get('Application/Permit #'),
                                            permit_type=permit_data.get('Type'),
                                            status=permit_data.get('Status'),
                                            description=permit_data.get('Work Description'),
                                            job_address=permit_data.get('Job Address'),
                                            contractor=permit_data.get('Contractor'),
                                            applicant=permit_data.get('Applicant'),
                                            issued_date=permit_data.get('Issued Date'),
                                            finaled_date=permit_data.get('Finaled Date'),
                                            expiration_date=permit_data.get('Expiration Date'),
                                            valuation=permit_data.get('Job Valuation')
                                        )
                                        
                                        permits.append(permit)
                                        print(f"[info] Added permit from container: {permit.permit_number} - {permit.permit_type}")
                except Exception as e:
                    print(f"[warn] Error processing container {i+1}: {e}")
            
            print(f"[info] Found {len(permits)} permits total")
            
        except Exception as e:
            print(f"[error] Error during scraping: {e}")
        finally:
            browser.close()
    
    return permits

def write_outputs(address_id: str, profile: ParcelProfile, permits: List[PermitRecord]) -> None:
    # JSON
    out_json = {
        "address_id": address_id,
        "parcel_profile": asdict(profile),
        "permits": [asdict(p) for p in permits],
        "source": {
            "results_url": f"{BASE}/PermitResults/{address_id}",
            "parcel_profile_url": f"{BASE}/ParcelProfileDetail/{address_id}",
        },
    }
    json_path = f"simple_ladbs_{address_id}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(out_json, f, ensure_ascii=False, indent=2)
    print(f"[info] Wrote {json_path}")

    # CSV
    csv_path = f"simple_ladbs_{address_id}.csv"
    fieldnames = [f for f in PermitRecord.__dataclass_fields__.keys()]
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for p in permits:
            w.writerow(asdict(p))
    print(f"[info] Wrote {csv_path}")

def main():
    ap = argparse.ArgumentParser(description="Simple LADBS permit scraper")
    ap.add_argument("--address-id", type=str, help="LADBS addressId (e.g., 1133872)")
    ap.add_argument("--url", type=str, help="Full PermitResults URL")
    ap.add_argument("--headed", action="store_true", help="Show the browser")
    args = ap.parse_args()

    if not args.address_id and not args.url:
        ap.error("Provide --address-id or --url")

    address_id = args.address_id
    if not address_id and args.url:
        try:
            path = urlparse(args.url).path.strip("/")
            parts = path.split("/")
            if "PermitResults" in parts:
                idx = parts.index("PermitResults")
                if idx + 1 < len(parts):
                    address_id = parts[idx + 1]
        except:
            pass

    if not address_id:
        ap.error("Could not determine addressId from input")

    print(f"[info] Starting simple permit scraper for address ID: {address_id}")
    
    # Get parcel profile
    try:
        profile = fetch_parcel_profile(address_id)
        print(f"[info] Parcel profile: PIN={profile.pin}, Zoning={profile.zoning}")
    except Exception as e:
        print(f"[warn] Could not fetch parcel profile: {e}")
        profile = ParcelProfile(address_id=address_id)
    
    # Scrape permits
    permits = scrape_permits_simple(address_id, headless=not args.headed)
    
    # Write outputs
    write_outputs(address_id, profile, permits)
    
    print(f"[success] Done! Found {len(permits)} permits")

if __name__ == "__main__":
    main()
