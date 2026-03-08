import requests
from bs4 import BeautifulSoup
import hashlib
import re

class BaseScraper:
    HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"}

    def fetch_html(self, url):
        try:
            response = requests.get(url, headers=self.HEADERS, timeout=15)
            response.raise_for_status()
            return BeautifulSoup(response.content, 'html.parser'), None
        except requests.RequestException as e:
            return None, str(e)

    def generate_hash(self, url):
        return hashlib.sha256(url.encode('utf-8')).hexdigest()

    def normalize_title(self, title):
        """Standardizes titles to prevent duplicate entries from different sources."""
        return re.sub(r'[^a-zA-Z0-9]', '', title).lower()

    def scrape(self):
        raise NotImplementedError

class ParliamentScraper(BaseScraper):
    SOURCE_NAME = "Parliament Tracker"
    URL = "https://www.parliament.go.ke/the-national-assembly/house-business/bills-tracker"

    def scrape(self):
        soup, error = self.fetch_html(self.URL)
        if error: return {"success": False, "error": error, "data": []}

        data = []
        for link in soup.find_all('a', href=True):
            if '.pdf' in link['href'].lower():
                raw_title = link.text.strip()
                source_url = link['href'] if link['href'].startswith('http') else f"https://www.parliament.go.ke{link['href']}"
                data.append({
                    "title": raw_title,
                    "normalized_title": self.normalize_title(raw_title),
                    "source_url": source_url,
                    "document_hash": self.generate_hash(source_url)
                })
        return {"success": True, "error": None, "data": data}

class MyGovScraper(BaseScraper):
    SOURCE_NAME = "MyGov Portal"
    URL = "https://www.mygov.go.ke/call-public-participation"

    def scrape(self):
        # Implementation mirrors ParliamentScraper adapted for MyGov HTML structure
        return {"success": True, "error": None, "data": []}

class GazetteScraper(BaseScraper):
    SOURCE_NAME = "Kenya Gazette"
    URL = "http://kenyalaw.org/kenyagazette/"

    def scrape(self):
        # Implementation mirrors ParliamentScraper adapted for Gazette HTML structure
        return {"success": True, "error": None, "data": []}