import hashlib
import re
from typing import Optional

import requests
from bs4 import BeautifulSoup

# Kenya's 47 counties (longest names first for substring matching).
KENYA_COUNTIES = (
    "Tharaka Nithi",
    "Elgeyo Marakwet",
    "Taita Taveta",
    "West Pokot",
    "Trans Nzoia",
    "Uasin Gishu",
    "Homa Bay",
    "Tana River",
    "Murang'a",
    "Mombasa",
    "Kwale",
    "Kilifi",
    "Lamu",
    "Garissa",
    "Wajir",
    "Mandera",
    "Marsabit",
    "Isiolo",
    "Meru",
    "Embu",
    "Kitui",
    "Machakos",
    "Makueni",
    "Nyandarua",
    "Nyeri",
    "Kirinyaga",
    "Kiambu",
    "Turkana",
    "Samburu",
    "Nandi",
    "Baringo",
    "Laikipia",
    "Nakuru",
    "Narok",
    "Kajiado",
    "Kericho",
    "Bomet",
    "Kakamega",
    "Vihiga",
    "Bungoma",
    "Busia",
    "Siaya",
    "Kisumu",
    "Migori",
    "Kisii",
    "Nyamira",
    "Nairobi",
)


def match_county_in_text(text: str) -> Optional[str]:
    """Return canonical county name if title/URL clearly references one."""
    if not text or not text.strip():
        return None
    haystack = text
    haystack_lower = haystack.lower()
    for county in sorted(KENYA_COUNTIES, key=len, reverse=True):
        if " " in county:
            if county.lower() in haystack_lower:
                return county
            continue
        variants = [county]
        if "'" in county:
            variants.append(county.replace("'", ""))
        for v in variants:
            if re.search(rf"\b{re.escape(v)}\b", haystack, re.IGNORECASE):
                return county
    return None


def classify_scraped_item(title: str, source_url: str, source: str) -> tuple[str, str]:
    """
    Returns (government_level, county) using Bill.GovernmentLevel codes.
    Parliament National Assembly tracker is treated as national-only.
    MyGov / Gazette use title + URL to detect county-level items.
    """
    from bills.models import Bill

    national = (Bill.GovernmentLevel.NATIONAL, "")
    url = (source_url or "").strip()
    t = (title or "").strip()
    blob = f"{t} {url}"

    if source == "parliament":
        return national

    county = match_county_in_text(blob)
    if county:
        return Bill.GovernmentLevel.COUNTY, county

    lower = blob.lower()
    county_signals = (
        "county government",
        "county assembly",
        "county public participation",
        "ward ",
        " sub-county",
        "subcounty",
        "cec member",
        "county executive",
    )
    if any(s in lower for s in county_signals):
        guessed = match_county_in_text(blob)
        if guessed:
            return Bill.GovernmentLevel.COUNTY, guessed

    return national


class BaseScraper:
    HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"}

    def fetch_html(self, url):
        try:
            response = requests.get(url, headers=self.HEADERS, timeout=15)
            response.raise_for_status()
            return BeautifulSoup(response.content, "html.parser"), None
        except requests.RequestException as e:
            return None, str(e)

    def generate_hash(self, url):
        return hashlib.sha256(url.encode("utf-8")).hexdigest()

    def normalize_title(self, title):
        """Standardizes titles to prevent duplicate entries from different sources."""
        return re.sub(r"[^a-zA-Z0-9]", "", title).lower()

    def scrape(self):
        raise NotImplementedError


class ParliamentScraper(BaseScraper):
    SOURCE_NAME = "Parliament Tracker"
    URL = "https://www.parliament.go.ke/the-national-assembly/house-business/bills-tracker"
    SOURCE_KEY = "parliament"

    def scrape(self):
        soup, error = self.fetch_html(self.URL)
        if error:
            return {"success": False, "error": error, "data": []}

        data = []
        for link in soup.find_all("a", href=True):
            if ".pdf" in link["href"].lower():
                raw_title = link.text.strip()
                source_url = (
                    link["href"] if link["href"].startswith("http") else f"https://www.parliament.go.ke{link['href']}"
                )
                level, county = classify_scraped_item(raw_title, source_url, self.SOURCE_KEY)
                data.append(
                    {
                        "title": raw_title,
                        "normalized_title": self.normalize_title(raw_title),
                        "source_url": source_url,
                        "document_hash": self.generate_hash(source_url),
                        "government_level": level,
                        "county": county,
                    }
                )
        return {"success": True, "error": None, "data": data}


class MyGovScraper(BaseScraper):
    SOURCE_NAME = "MyGov Portal"
    URL = "https://www.mygov.go.ke/call-public-participation"
    SOURCE_KEY = "mygov"

    def scrape(self):
        # Parse HTML like ParliamentScraper; for each item call:
        # level, county = classify_scraped_item(title, source_url, self.SOURCE_KEY)
        # and include "government_level" and "county" on the dict.
        return {"success": True, "error": None, "data": []}


class GazetteScraper(BaseScraper):
    SOURCE_NAME = "Kenya Gazette"
    URL = "http://kenyalaw.org/kenyagazette/"
    SOURCE_KEY = "gazette"

    def scrape(self):
        # Same as MyGovScraper docstring: use classify_scraped_item per row.
        return {"success": True, "error": None, "data": []}
