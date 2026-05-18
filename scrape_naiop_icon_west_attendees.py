#!/usr/bin/env python3
"""
Scrape NAIOP I.CON West 2026 attendees table into a CSV.

Source page:
https://www.naiop.org/events-and-sponsorship/corporate-events-list/conferences/2026-icon-west-the-industrial-conference/attendees/

This script fetches the page HTML, extracts the attendees table, and saves it as CSV.
"""

from __future__ import annotations

import argparse
import html as html_lib
import re
import sys
import time
import ssl
from html.parser import HTMLParser
from urllib.request import Request, urlopen
from urllib.parse import urljoin, urlparse

import pandas as pd


DEFAULT_URL = (
    "https://www.naiop.org/events-and-sponsorship/corporate-events-list/"
    "conferences/2026-icon-west-the-industrial-conference/attendees/"
)


def fetch_html(url: str, *, verify_ssl: bool = True) -> str:
    req = Request(
        url,
        headers={
            # Some sites block default Python user agents.
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        },
    )
    ctx = None
    if not verify_ssl:
        # WARNING: Disables TLS certificate verification. Use only if you trust the URL.
        ctx = ssl._create_unverified_context()

    with urlopen(req, timeout=30, context=ctx) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="replace")


def extract_pagination_urls(html: str, base_url: str) -> set[str]:
    """
    Best-effort pagination discovery.

    Many event attendee pages use standard query params (e.g. ?page=2) or /page/2/ patterns.
    We collect any hrefs that:
    - resolve under the same host
    - contain 'attendees' in the path
    - look like a paginated variant
    """
    urls: set[str] = set()

    for href in re.findall(r'href=["\']([^"\']+)["\']', html, flags=re.IGNORECASE):
        href = href.strip()
        if not href or href.startswith("#") or href.lower().startswith("javascript:"):
            continue

        abs_url = urljoin(base_url, href)

        try:
            parsed = urlparse(abs_url)
            base_parsed = urlparse(base_url)
        except Exception:
            continue

        if parsed.netloc and base_parsed.netloc and parsed.netloc != base_parsed.netloc:
            continue

        path_lower = (parsed.path or "").lower()
        if "attendees" not in path_lower:
            continue

        q = (parsed.query or "").lower()
        if (
            "page=" in q
            or "paged=" in q
            or "p=" in q
            or re.search(r"/page/\d+/?$", path_lower)
        ):
            urls.add(abs_url)

    return urls


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []

    def handle_data(self, data: str) -> None:
        if data:
            self._chunks.append(data)

    def text(self) -> str:
        return " ".join(" ".join(self._chunks).split()).strip()


def _html_to_text(fragment: str) -> str:
    parser = _TextExtractor()
    parser.feed(fragment)
    parser.close()
    return html_lib.unescape(parser.text())


def _extract_tables_html(html: str) -> list[str]:
    # Very simple table extraction; good enough for well-formed HTML.
    return re.findall(r"(?is)<table\b[^>]*>.*?</table>", html)


def _parse_table_html(table_html: str) -> pd.DataFrame:
    """
    Parse an HTML <table> into a DataFrame without external parsers (no lxml/bs4).
    """
    rows = re.findall(r"(?is)<tr\b[^>]*>(.*?)</tr>", table_html)
    parsed_rows: list[list[str]] = []
    for row_html in rows:
        # Prefer <th>/<td> cells; if none, skip.
        cells = re.findall(r"(?is)<t[hd]\b[^>]*>(.*?)</t[hd]>", row_html)
        if not cells:
            continue
        parsed_rows.append([_html_to_text(c) for c in cells])

    if not parsed_rows:
        return pd.DataFrame()

    # Header row: use first row if it looks like headers.
    header = parsed_rows[0]
    data_rows = parsed_rows[1:]

    # If data rows don't match header length, pad/truncate.
    def norm_len(r: list[str], n: int) -> list[str]:
        if len(r) == n:
            return r
        if len(r) < n:
            return r + [""] * (n - len(r))
        return r[:n]

    ncols = len(header)
    data_rows = [norm_len(r, ncols) for r in data_rows]

    return pd.DataFrame(data_rows, columns=header)


def find_attendees_table(tables: list[pd.DataFrame]) -> pd.DataFrame:
    """
    Pick the table that looks like the attendee list:
    columns similar to First Name / Last Name / Company / Title / Chapter.
    """
    expected = {"first name", "last name", "company", "title"}

    best = None
    best_score = -1
    for t in tables:
        cols = [str(c).strip().lower() for c in t.columns]
        score = len(expected.intersection(set(cols)))
        if score > best_score:
            best = t
            best_score = score

    if best is None or best_score < 3:
        raise ValueError("Could not locate attendees table in parsed HTML.")

    return best


def clean_df(df: pd.DataFrame) -> pd.DataFrame:
    # Normalize column names
    df = df.copy()
    df.columns = [re.sub(r"\s+", " ", str(c)).strip() for c in df.columns]

    # Drop fully empty rows
    df = df.dropna(how="all")

    # Strip whitespace in string cells
    for col in df.columns:
        if df[col].dtype == object:
            df[col] = df[col].astype(str).map(lambda x: x.strip())
            # Turn "nan" strings back into empty
            df.loc[df[col].str.lower() == "nan", col] = ""

    return df


def scrape_all_pages(
    start_url: str,
    *,
    delay_s: float = 0.5,
    max_pages: int = 200,
    verify_ssl: bool = True,
) -> pd.DataFrame:
    """
    Crawl attendee pages starting from start_url, following pagination links when present.
    Returns a merged, deduped DataFrame.
    """
    visited: set[str] = set()
    to_visit: list[str] = [start_url]
    all_frames: list[pd.DataFrame] = []

    while to_visit and len(visited) < max_pages:
        url = to_visit.pop(0)
        if url in visited:
            continue
        visited.add(url)

        html = fetch_html(url, verify_ssl=verify_ssl)

        # Parse tables on this page (no lxml).
        try:
            table_htmls = _extract_tables_html(html)
            tables = [_parse_table_html(t) for t in table_htmls]
            tables = [t for t in tables if not t.empty]
            attendees = find_attendees_table(tables)
            attendees = clean_df(attendees)
            if len(attendees) > 0:
                all_frames.append(attendees)
        except ValueError:
            # No tables / no attendee table on this page; keep crawling via discovered links.
            pass

        # Discover more pagination URLs from this page.
        for next_url in sorted(extract_pagination_urls(html, url)):
            if next_url not in visited and next_url not in to_visit:
                to_visit.append(next_url)

        if delay_s > 0:
            time.sleep(delay_s)

    if not all_frames:
        raise ValueError("No attendee rows found across crawled pages.")

    merged = pd.concat(all_frames, ignore_index=True).drop_duplicates()

    # If canonical columns exist, dedupe more aggressively on them.
    canon_cols = ["First Name", "Last Name", "Company", "Title"]
    if all(c in merged.columns for c in canon_cols):
        merged = merged.drop_duplicates(subset=canon_cols, keep="first")

    return merged


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Scrape NAIOP I.CON West 2026 attendees into CSV."
    )
    parser.add_argument("--url", default=DEFAULT_URL, help="Attendees page URL")
    parser.add_argument(
        "--output",
        default="naiop_icon_west_2026_attendees.csv",
        help="Output CSV filename",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="Delay (seconds) between page fetches when crawling pagination",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=200,
        help="Safety cap for number of pages to crawl",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Disable SSL certificate verification (only use if you trust the URL)",
    )
    args = parser.parse_args()

    attendees = scrape_all_pages(
        args.url,
        delay_s=args.delay,
        max_pages=args.max_pages,
        verify_ssl=(not args.insecure),
    )

    attendees.to_csv(args.output, index=False)

    print(f"✅ Saved {len(attendees)} attendees to: {args.output}")
    print(f"   Columns: {list(attendees.columns)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

