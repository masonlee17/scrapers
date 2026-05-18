#!/usr/bin/env python3
"""
Address-Based LADBS Permit Scraper

This script takes a street address as input, finds the corresponding LADBS address ID,
and then scrapes all permits for that address.

Usage Examples:
# Activate virtual environment
source brand_radar/venv/bin/activate

# Basic usage - just provide street number and name
python address_permitscraper.py --street-number 10915 --street-name STRATHMORE

# With debug mode (shows browser)
python address_permitscraper.py --street-number 10915 --street-name STRATHMORE --headed

# Try different addresses
python address_permitscraper.py --street-number 123 --street-name MAIN
python address_permitscraper.py --street-number 456 --street-name OAK
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
SEARCH_URL = f"{BASE}/?service=plr"

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

@dataclass
class CertificateOfOccupancyRecord:
    address_id: str
    co_number: Optional[str] = None
    date: Optional[str] = None
    status: Optional[str] = None
    description: Optional[str] = None
    co_url: str = ""
    associated_permit: Optional[str] = None
    permit_type: Optional[str] = None
    permit_status: Optional[str] = None
    building_safety_status: Optional[str] = None
    public_works_status: Optional[str] = None
    hcidla_status: Optional[str] = None
    lafd_status: Optional[str] = None
    rec_parks_status: Optional[str] = None
    aqmd_status: Optional[str] = None
    all_associated_permits: Optional[str] = None
    department_approval_summary: Optional[str] = None

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

def find_address_id(street_number: str, street_name: str, headless: bool = True) -> Optional[str]:
    """Find the LADBS address ID for a given street address"""
    print(f"[info] Searching for address: {street_number} {street_name}")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx = browser.new_context()
        page = ctx.new_page()
        
        try:
            # Navigate to the search page
            print(f"[info] Loading search page...")
            page.goto(SEARCH_URL, wait_until="networkidle", timeout=60000)
            
            # Fill in the address form
            print(f"[info] Filling address form...")
            
            # Find and fill street number field - try different selectors
            street_number_field = None
            for selector in ['input[name="StreetNumber"]', 'input[placeholder*="Street Number"]', 'input[type="text"]']:
                field = page.query_selector(selector)
                if field:
                    street_number_field = field
                    break
            
            if street_number_field:
                street_number_field.fill(street_number)
                print(f"[info] Entered street number: {street_number}")
            else:
                print(f"[warn] Could not find street number field")
                # Debug: show all input fields
                inputs = page.query_selector_all('input')
                print(f"[debug] Found {len(inputs)} input fields:")
                for i, inp in enumerate(inputs):
                    name = inp.get_attribute('name') or 'no-name'
                    placeholder = inp.get_attribute('placeholder') or 'no-placeholder'
                    print(f"[debug] Input {i+1}: name='{name}' placeholder='{placeholder}'")
                return None
            
            # Find and fill street name field - try different selectors
            street_name_field = None
            for selector in ['input[name="StreetName"]', 'input[placeholder*="Street Name"]', 'input[type="text"]']:
                fields = page.query_selector_all(selector)
                if len(fields) > 1:  # Take the second text input (street name)
                    street_name_field = fields[1]
                    break
                elif len(fields) == 1 and fields[0] != street_number_field:
                    street_name_field = fields[0]
                    break
            
            if street_name_field:
                street_name_field.fill(street_name)
                print(f"[info] Entered street name: {street_name}")
            else:
                print(f"[warn] Could not find street name field")
                return None
            
            # Submit the form
            print(f"[info] Submitting search form...")
            submit_button = page.query_selector('input[type="submit"], button[type="submit"]')
            if submit_button:
                submit_button.click()
            else:
                # Try pressing Enter
                page.keyboard.press("Enter")
            
            # Wait for results
            page.wait_for_timeout(3000)
            
            # Look for address results
            print(f"[info] Looking for address results...")
            
            # Check if we got redirected to a results page
            current_url = page.url
            print(f"[info] Current URL: {current_url}")
            
            # Look for address ID in the URL
            if "PermitResults" in current_url:
                # Extract address ID from URL
                path = urlparse(current_url).path.strip("/")
                parts = path.split("/")
                if "PermitResults" in parts:
                    idx = parts.index("PermitResults")
                    if idx + 1 < len(parts):
                        address_id = parts[idx + 1]
                        print(f"[info] Found address ID in URL: {address_id}")
                        return address_id
            
            # Look for address links on the page
            address_links = page.query_selector_all('a[href*="PermitResults"]')
            print(f"[info] Found {len(address_links)} address result links")
            
            for i, link in enumerate(address_links):
                href = link.get_attribute("href") or ""
                text = link.inner_text().strip()
                print(f"[info] Link {i+1}: {text} -> {href}")
                
                # Extract address ID from href
                if "PermitResults" in href:
                    path = urlparse(href).path.strip("/")
                    parts = path.split("/")
                    if "PermitResults" in parts:
                        idx = parts.index("PermitResults")
                        if idx + 1 < len(parts):
                            address_id = parts[idx + 1]
                            print(f"[info] Found address ID: {address_id}")
                            return address_id
            
            # Look for any text that might contain an address ID
            page_content = page.content()
            address_id_match = re.search(r'PermitResults/(\d+)', page_content)
            if address_id_match:
                address_id = address_id_match.group(1)
                print(f"[info] Found address ID in page content: {address_id}")
                return address_id
            
            print(f"[warn] Could not find address ID for {street_number} {street_name}")
            return None
            
        except Exception as e:
            print(f"[error] Error searching for address: {e}")
            return None
        finally:
            browser.close()

def fetch_co_detail_with_playwright(co_number: str, address_id: str, permits: List[PermitRecord]) -> List[CertificateOfOccupancyRecord]:
    """Fetch detailed CO information using Playwright to click dropdowns and extract all data"""
    url = f"{BASE}/PermitReport/CofODetail/{co_number}"
    print(f"[info] Fetching CO detail with Playwright for {co_number}: {url}")
    
    try:
        # Use a separate Playwright instance to avoid conflicts
        import subprocess
        import tempfile
        import json
        
        # Create a temporary script to run Playwright
        script_content = f'''
import asyncio
from playwright.async_api import async_playwright
import json

async def scrape_co_detail():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        await page.goto("{url}")
        await page.wait_for_load_state("networkidle")
        
        # Extract basic info
        co_number = await page.text_content('text="Certificate Number"')
        co_number = co_number.split('Certificate Number')[-1].strip() if co_number else None
        
        status = await page.text_content('text="CofO Status"')
        status = status.split('CofO Status')[-1].strip() if status else None
        
        permits_text = await page.text_content('text="Associated Permits"')
        permits_text = permits_text.split('Associated Permits')[-1].strip() if permits_text else None
        
        # Click each department dropdown and extract data
        departments = [
            ("Building and Safety", "building_safety"),
            ("Public Works", "public_works"),
            ("HCIDLA Housing Dept", "hcidla"),
            ("LAFD", "lafd"),
            ("Rec and Parks", "rec_parks"),
            ("Air Quality Management District", "aqmd")
        ]
        
        dept_data = {{}}
        for dept_name, dept_key in departments:
            try:
                # Click the department section
                await page.click(f'text="{dept_name}"')
                await page.wait_for_timeout(1000)
                
                # Extract status and any additional details
                dept_element = await page.query_selector(f'text="{dept_name}"')
                if dept_element:
                    content = await dept_element.text_content()
                    if "OK for CofO" in content:
                        dept_data[dept_key] = "OK for CofO"
                    elif "Pending" in content:
                        dept_data[dept_key] = "Pending"
                    else:
                        dept_data[dept_key] = "Unknown"
                else:
                    dept_data[dept_key] = "Not Found"
            except Exception as e:
                dept_data[dept_key] = f"Error: {{str(e)}}"
        
        await browser.close()
        
        result = {{
            "co_number": co_number,
            "status": status,
            "associated_permits": permits_text,
            "departments": dept_data
        }}
        
        print(json.dumps(result))

asyncio.run(scrape_co_detail())
'''
        
        # Write and run the script
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            f.write(script_content)
            script_path = f.name
        
        try:
            result = subprocess.run(['python', script_path], capture_output=True, text=True, timeout=30)
            if result.returncode == 0:
                # Parse the JSON output
                output_lines = result.stdout.strip().split('\n')
                json_line = None
                for line in output_lines:
                    if line.startswith('{'):
                        json_line = line
                        break
                
                if json_line:
                    data = json.loads(json_line)
                    print(f"[info] Playwright extracted: {data}")
                    
                    # Use the extracted data
                    co_number_value = data.get('co_number')
                    status_value = data.get('status')
                    associated_permits_text = data.get('associated_permits')
                    dept_data = data.get('departments', {})
                    
                    building_safety_status = dept_data.get('building_safety')
                    public_works_status = dept_data.get('public_works')
                    hcidla_status = dept_data.get('hcidla')
                    lafd_status = dept_data.get('lafd')
                    rec_parks_status = dept_data.get('rec_parks')
                    aqmd_status = dept_data.get('aqmd')
                    
                    print(f"[info] Department statuses from Playwright:")
                    print(f"[info]  Building & Safety: {building_safety_status}")
                    print(f"[info]  Public Works: {public_works_status}")
                    print(f"[info]  HCIDLA: {hcidla_status}")
                    print(f"[info]  LAFD: {lafd_status}")
                    print(f"[info]  Rec & Parks: {rec_parks_status}")
                    print(f"[info]  AQMD: {aqmd_status}")
                    
                    # Continue with the rest of the function using this data
                    return create_co_records_from_data(
                        co_number_value, status_value, associated_permits_text,
                        building_safety_status, public_works_status, hcidla_status,
                        lafd_status, rec_parks_status, aqmd_status,
                        address_id, permits, url
                    )
                else:
                    print(f"[warn] No JSON output from Playwright script")
            else:
                print(f"[warn] Playwright script failed: {result.stderr}")
        finally:
            import os
            os.unlink(script_path)
            
    except Exception as e:
        print(f"[warn] Playwright approach failed: {e}")
    
    # Fallback to static HTML approach
    return fetch_co_detail_static(co_number, address_id, permits)

def create_co_records_from_data(co_number_value, status_value, associated_permits_text,
                               building_safety_status, public_works_status, hcidla_status,
                               lafd_status, rec_parks_status, aqmd_status,
                               address_id, permits, url):
    """Create CO records from extracted data"""
    # Create department approval summary
    dept_statuses = [
        f"Building & Safety: {building_safety_status or 'Unknown'}",
        f"Public Works: {public_works_status or 'Unknown'}",
        f"HCIDLA: {hcidla_status or 'Unknown'}",
        f"LAFD: {lafd_status or 'Unknown'}",
        f"Rec & Parks: {rec_parks_status or 'Unknown'}",
        f"AQMD: {aqmd_status or 'Unknown'}"
    ]
    department_approval_summary = "; ".join(dept_statuses)
    
    # Parse associated permits and create one record per permit
    certificates = []
    if associated_permits_text:
        # Split by common delimiters and clean up
        permit_numbers = re.split(r'[,\s]+', associated_permits_text)
        permit_numbers = [p.strip() for p in permit_numbers if p.strip() and p.strip() != '']
        
        # Remove duplicates while preserving order
        seen = set()
        unique_permits = []
        for p in permit_numbers:
            if p not in seen:
                seen.add(p)
                unique_permits.append(p)
        
        print(f"[info] Found {len(unique_permits)} associated permits: {unique_permits}")
        
        for permit_number in unique_permits:
            # Look up permit details from our permits list
            permit_details = None
            for permit in permits:
                if permit.permit_number == permit_number:
                    permit_details = permit
                    break
            
            # Create certificate record for this permit
            certificate = CertificateOfOccupancyRecord(
                address_id=address_id,
                co_number=co_number_value or co_number,
                status=status_value,
                co_url=url,
                associated_permit=permit_number,
                permit_type=permit_details.permit_type if permit_details else None,
                permit_status=permit_details.status if permit_details else None,
                building_safety_status=building_safety_status,
                public_works_status=public_works_status,
                hcidla_status=hcidla_status,
                lafd_status=lafd_status,
                rec_parks_status=rec_parks_status,
                aqmd_status=aqmd_status,
                all_associated_permits=associated_permits_text,
                department_approval_summary=department_approval_summary
            )
            certificates.append(certificate)
            print(f"[info] Created CO record for permit {permit_number} - {permit_details.permit_type if permit_details else 'Unknown Type'}")
    else:
        # If no associated permits found, create one record without permit details
        certificate = CertificateOfOccupancyRecord(
            address_id=address_id,
            co_number=co_number_value or co_number,
            status=status_value,
            co_url=url,
            building_safety_status=building_safety_status,
            public_works_status=public_works_status,
            hcidla_status=hcidla_status,
            lafd_status=lafd_status,
            rec_parks_status=rec_parks_status,
            aqmd_status=aqmd_status,
            department_approval_summary=department_approval_summary
        )
        certificates.append(certificate)
        print(f"[info] Created CO record without associated permits")
    
    return certificates

def fetch_co_detail_static(co_number: str, address_id: str, permits: List[PermitRecord]) -> List[CertificateOfOccupancyRecord]:
    """Fetch detailed Certificate of Occupancy information from CO detail page and create one record per associated permit"""
    url = f"{BASE}/PermitReport/CofODetail/{co_number}"
    print(f"[info] Fetching CO detail for {co_number}: {url}")
    
    try:
        # Use requests + BeautifulSoup for basic info, then try to get more detailed info
        resp = request_get(url)
        soup = BeautifulSoup(resp.text, "lxml")
        
        # Extract basic information
        co_number_text = soup.find(string=re.compile(r"Certificate Number", re.I))
        co_number_value = None
        if co_number_text:
            co_number_el = co_number_text.find_next()
            if co_number_el:
                co_number_value = clean_text(co_number_el.get_text())
        
        # Extract associated permits
        associated_permits_text = None
        permits_text = soup.find(string=re.compile(r"Associated Permits", re.I))
        if permits_text:
            permits_el = permits_text.find_next()
            if permits_el:
                associated_permits_text = clean_text(permits_el.get_text())
        
        # Extract CO status
        status_text = soup.find(string=re.compile(r"CofO Status", re.I))
        status_value = None
        if status_text:
            status_el = status_text.find_next()
            if status_el:
                status_value = clean_text(status_el.get_text())
        
        # Try to extract department statuses from the static HTML
        # Look for department sections and their statuses
        def find_department_status_advanced(dept_name: str) -> Optional[str]:
            # Look for the department name in various formats
            dept_patterns = [
                f"{dept_name}",
                f"{dept_name} Dept",
                f"{dept_name} Department"
            ]
            
            for pattern in dept_patterns:
                dept_text = soup.find(string=re.compile(pattern, re.I))
                if dept_text:
                    # Look for status in the same element or nearby
                    parent = dept_text.parent
                    if parent:
                        # Check if status is in the same element
                        text_content = parent.get_text()
                        if "OK for CofO" in text_content:
                            return "OK for CofO"
                        elif "Pending" in text_content:
                            return "Pending"
                        elif "loading" in text_content.lower():
                            return "Loading"
                    
                    # Look in next sibling elements
                    next_elem = parent.find_next_sibling()
                    if next_elem:
                        next_text = next_elem.get_text()
                        if "OK for CofO" in next_text:
                            return "OK for CofO"
                        elif "Pending" in next_text:
                            return "Pending"
                        elif "loading" in next_text.lower():
                            return "Loading"
            return None
        
        building_safety_status = find_department_status_advanced("Building and Safety")
        public_works_status = find_department_status_advanced("Public Works")
        hcidla_status = find_department_status_advanced("HCIDLA Housing")
        lafd_status = find_department_status_advanced("LAFD")
        rec_parks_status = find_department_status_advanced("Rec and Parks")
        aqmd_status = find_department_status_advanced("Air Quality Management District")
        
        print(f"[info] Department statuses found:")
        print(f"[info]  Building & Safety: {building_safety_status}")
        print(f"[info]  Public Works: {public_works_status}")
        print(f"[info]  HCIDLA: {hcidla_status}")
        print(f"[info]  LAFD: {lafd_status}")
        print(f"[info]  Rec & Parks: {rec_parks_status}")
        print(f"[info]  AQMD: {aqmd_status}")
        
        # Save debug HTML for analysis
        debug_file = f"debug_co_{co_number}.html"
        with open(debug_file, "w", encoding="utf-8") as f:
            f.write(resp.text)
        print(f"[info] Saved debug HTML to {debug_file}")
        
        # Create department approval summary
        dept_statuses = [
            f"Building & Safety: {building_safety_status or 'Unknown'}",
            f"Public Works: {public_works_status or 'Unknown'}",
            f"HCIDLA: {hcidla_status or 'Unknown'}",
            f"LAFD: {lafd_status or 'Unknown'}",
            f"Rec & Parks: {rec_parks_status or 'Unknown'}",
            f"AQMD: {aqmd_status or 'Unknown'}"
        ]
        department_approval_summary = "; ".join(dept_statuses)
        
        # Parse associated permits and create one record per permit
        certificates = []
        if associated_permits_text:
            # Split by common delimiters and clean up
            permit_numbers = re.split(r'[,\s]+', associated_permits_text)
            permit_numbers = [p.strip() for p in permit_numbers if p.strip() and p.strip() != '']
            
            # Remove duplicates while preserving order
            seen = set()
            unique_permits = []
            for p in permit_numbers:
                if p not in seen:
                    seen.add(p)
                    unique_permits.append(p)
            
            print(f"[info] Found {len(unique_permits)} associated permits: {unique_permits}")
            
            for permit_number in unique_permits:
                # Look up permit details from our permits list
                permit_details = None
                for permit in permits:
                    if permit.permit_number == permit_number:
                        permit_details = permit
                        break
                
                # Create certificate record for this permit
                certificate = CertificateOfOccupancyRecord(
                    address_id=address_id,
                    co_number=co_number_value or co_number,
                    status=status_value,
                    co_url=url,
                    associated_permit=permit_number,
                    permit_type=permit_details.permit_type if permit_details else None,
                    permit_status=permit_details.status if permit_details else None,
                    building_safety_status=building_safety_status,
                    public_works_status=public_works_status,
                    hcidla_status=hcidla_status,
                    lafd_status=lafd_status,
                    rec_parks_status=rec_parks_status,
                    aqmd_status=aqmd_status,
                    all_associated_permits=associated_permits_text,
                    department_approval_summary=department_approval_summary
                )
                certificates.append(certificate)
                print(f"[info] Created CO record for permit {permit_number} - {permit_details.permit_type if permit_details else 'Unknown Type'}")
        else:
            # If no associated permits found, create one record without permit details
            certificate = CertificateOfOccupancyRecord(
                address_id=address_id,
                co_number=co_number_value or co_number,
                status=status_value,
                co_url=url,
                building_safety_status=building_safety_status,
                public_works_status=public_works_status,
                hcidla_status=hcidla_status,
                lafd_status=lafd_status,
                rec_parks_status=rec_parks_status,
                aqmd_status=aqmd_status,
                department_approval_summary=department_approval_summary
            )
            certificates.append(certificate)
            print(f"[info] Created CO record without associated permits")
        
        return certificates
        
    except Exception as e:
        print(f"[warn] Error fetching CO detail for {co_number}: {e}")
        return [CertificateOfOccupancyRecord(
            address_id=address_id,
            co_number=co_number,
            co_url=url
        )]

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

def scrape_permits_for_address_id(address_id: str, headless: bool = True) -> Tuple[List[PermitRecord], List[CertificateOfOccupancyRecord]]:
    """Scrape permits and certificates of occupancy for a given address ID"""
    target = f"{BASE}/PermitReport/PermitResults/{address_id}"
    permits = []
    certificates = []
    
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
            
            # Look for individual permit summary sections that need to be clicked
            permit_summary_sections = page.query_selector_all('h3[id*="PermitSummary"]')
            print(f"[info] Found {len(permit_summary_sections)} permit summary sections")
            
            # Click each permit summary section to expand them
            for i, section in enumerate(permit_summary_sections):
                try:
                    section_id = section.get_attribute('id')
                    print(f"[info] Clicking permit summary section {i+1}: {section_id}")
                    section.click()
                    page.wait_for_timeout(1500)
                except Exception as e:
                    print(f"[warn] Could not click permit summary section {i+1}: {e}")
            
            # Wait for all sections to load
            page.wait_for_timeout(2000)
            
            # Look for permit data in tables
            print(f"[info] Looking for permit data...")
            tables = page.query_selector_all('table')
            print(f"[info] Found {len(tables)} tables")
            
            for i, table in enumerate(tables):
                rows = table.query_selector_all('tr')
                if len(rows) >= 2:  # Has header and data
                    headers = [cell.inner_text().strip() for cell in rows[0].query_selector_all('td, th')]
                    
                    # Check if this looks like a permit table
                    if any('permit' in h.lower() or 'application' in h.lower() for h in headers):
                        print(f"[info] Found permit table!")
                        
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
            
            print(f"[info] Found {len(permits)} permits total")
            
            # Now scrape Certificate of Occupancy data
            print(f"[info] Looking for Certificate of Occupancy data...")
            
            # Look for and click the Certificate of Occupancy section
            co_section = page.query_selector('h3[id="cofo"]')
            if co_section:
                print(f"[info] Found Certificate of Occupancy section! Clicking...")
                co_section.click()
                page.wait_for_timeout(2000)  # Wait for content to load
                print(f"[info] Waiting for CO content to load...")
                page.wait_for_load_state("networkidle", timeout=10000)
            else:
                print(f"[info] No Certificate of Occupancy section found")
            
            # Look for Certificate of Occupancy links in the page
            co_links = page.query_selector_all('a[href*="CofODetail"]')
            co_numbers = set()
            
            for link in co_links:
                href = link.get_attribute("href") or ""
                if "CofODetail" in href:
                    # Extract CO number from URL like /CofODetail/254515
                    match = re.search(r'/CofODetail/(\d+)', href)
                    if match:
                        co_number = match.group(1)
                        co_numbers.add(co_number)
                        print(f"[info] Found CO number: {co_number}")
            
            # Fetch detailed information for each CO
            for co_number in co_numbers:
                print(f"[info] Fetching detailed CO information for {co_number}...")
                co_details = fetch_co_detail_with_playwright(co_number, address_id, permits)
                certificates.extend(co_details)
                print(f"[info] Added {len(co_details)} CO records for CO {co_number}")
            
            print(f"[info] Found {len(certificates)} certificates of occupancy total")
            
        except Exception as e:
            print(f"[error] Error during scraping: {e}")
        finally:
            browser.close()
    
    return permits, certificates

def write_outputs(address: str, address_id: str, profile: ParcelProfile, permits: List[PermitRecord], certificates: List[CertificateOfOccupancyRecord]) -> None:
    # Clean address for filename
    clean_address = re.sub(r'[^\w\s-]', '', address).strip().replace(' ', '_')
    
    # JSON
    out_json = {
        "address": address,
        "address_id": address_id,
        "parcel_profile": asdict(profile),
        "permits": [asdict(p) for p in permits],
        "certificates_of_occupancy": [asdict(c) for c in certificates],
        "source": {
            "search_url": SEARCH_URL,
            "results_url": f"{BASE}/PermitReport/PermitResults/{address_id}",
            "parcel_profile_url": f"{BASE}/ParcelProfileDetail/{address_id}",
        },
    }
    json_path = f"address_permits_{clean_address}_{address_id}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(out_json, f, ensure_ascii=False, indent=2)
    print(f"[info] Wrote {json_path}")

    # CSV for permits
    csv_path = f"address_permits_{clean_address}_{address_id}.csv"
    fieldnames = [f for f in PermitRecord.__dataclass_fields__.keys()]
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for p in permits:
            w.writerow(asdict(p))
    print(f"[info] Wrote {csv_path}")
    
    # CSV for certificates of occupancy
    co_csv_path = f"address_certificates_{clean_address}_{address_id}.csv"
    co_fieldnames = [f for f in CertificateOfOccupancyRecord.__dataclass_fields__.keys()]
    with open(co_csv_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=co_fieldnames)
        w.writeheader()
        for c in certificates:
            w.writerow(asdict(c))
    print(f"[info] Wrote {co_csv_path}")

def main():
    ap = argparse.ArgumentParser(description="Address-based LADBS permit scraper")
    ap.add_argument("--street-number", type=str, required=True, help="Street number (e.g., 10915)")
    ap.add_argument("--street-name", type=str, required=True, help="Street name (e.g., STRATHMORE)")
    ap.add_argument("--headed", action="store_true", help="Show the browser")
    args = ap.parse_args()

    street_number = args.street_number
    street_name = args.street_name.upper()  # LADBS expects uppercase
    
    print(f"[info] Starting address-based permit scraper")
    print(f"[info] Address: {street_number} {street_name}")
    
    # Step 1: Find the address ID
    address_id = find_address_id(street_number, street_name, headless=not args.headed)
    
    if not address_id:
        print(f"[error] Could not find address ID for {street_number} {street_name}")
        print(f"[info] Please check the address and try again")
        sys.exit(1)
    
    print(f"[success] Found address ID: {address_id}")
    
    # Step 2: Get parcel profile
    try:
        profile = fetch_parcel_profile(address_id)
        print(f"[info] Parcel profile: PIN={profile.pin}, Zoning={profile.zoning}")
    except Exception as e:
        print(f"[warn] Could not fetch parcel profile: {e}")
        profile = ParcelProfile(address_id=address_id)
    
    # Step 3: Scrape permits and certificates
    permits, certificates = scrape_permits_for_address_id(address_id, headless=not args.headed)
    
    # Step 4: Write outputs
    full_address = f"{street_number} {street_name}"
    write_outputs(full_address, address_id, profile, permits, certificates)
    
    print(f"[success] Done! Found {len(permits)} permits and {len(certificates)} certificates for {full_address}")

if __name__ == "__main__":
    main()
