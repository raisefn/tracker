"""Multi-accelerator portfolio collector.

Scrapes portfolio/alumni pages from 20+ major accelerator programs.
Each accelerator has a config entry with its portfolio URL, selectors,
and program metadata (check size, equity, focus areas).

Creates RawRound entries with round_type="accelerator" so the ingest
pipeline creates Project + Round + Investor records automatically.
"""

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import date

import httpx
from bs4 import BeautifulSoup

from src.collectors.base import BaseCollector, RawRound

logger = logging.getLogger(__name__)

REQUEST_DELAY = 2  # seconds between requests to the same domain
MAX_COMPANIES_PER_PROGRAM = 500


@dataclass
class AcceleratorProgram:
    """Configuration for scraping one accelerator's portfolio."""

    name: str
    slug: str  # used as investor name
    portfolio_url: str
    check_size_usd: int
    equity_pct: float | None = None  # e.g. 0.07 for 7%
    focus: list[str] | None = None  # sector focus areas
    location: str | None = None
    website: str | None = None
    # CSS selectors for parsing
    company_selector: str = "a"  # selector for company links/cards
    name_selector: str | None = None  # selector within card for name
    desc_selector: str | None = None  # selector within card for description
    # Pagination
    paginated: bool = False
    page_param: str = "page"
    max_pages: int = 10
    # API mode (JSON instead of HTML)
    is_api: bool = False


# ── Accelerator registry ──────────────────────────────────────────────

ACCELERATORS: list[AcceleratorProgram] = [
    AcceleratorProgram(
        name="Antler",
        slug="antler",
        portfolio_url="https://www.antler.co/portfolio",
        check_size_usd=100_000,
        equity_pct=0.10,
        focus=["generalist"],
        location="Global",
        website="https://antler.co",
        company_selector=".portfolio-card, [data-testid='portfolio-card'], article",
    ),
    AcceleratorProgram(
        name="Seedcamp",
        slug="seedcamp",
        portfolio_url="https://seedcamp.com/portfolio/",
        check_size_usd=100_000,
        equity_pct=0.075,
        focus=["fintech", "saas", "ai"],
        location="London, UK",
        website="https://seedcamp.com",
        company_selector=".portfolio-item, .company-card, article",
    ),
    AcceleratorProgram(
        name="Plug and Play Tech Center",
        slug="plug-and-play",
        portfolio_url="https://www.plugandplaytechcenter.com/portfolio",
        check_size_usd=25_000,
        equity_pct=None,  # varies
        focus=["fintech", "healthtech", "mobility", "supply_chain"],
        location="Sunnyvale, CA",
        website="https://plugandplaytechcenter.com",
        company_selector=".portfolio-card, .company-item, article",
    ),
    AcceleratorProgram(
        name="MassChallenge",
        slug="masschallenge",
        portfolio_url="https://masschallenge.org/startups/",
        check_size_usd=0,  # no-equity accelerator, but gives cash prizes
        equity_pct=0.0,
        focus=["generalist"],
        location="Boston, MA",
        website="https://masschallenge.org",
        company_selector=".startup-card, .company-card, article",
    ),
    AcceleratorProgram(
        name="Alchemist Accelerator",
        slug="alchemist-accelerator",
        portfolio_url="https://alchemistaccelerator.com/portfolio/",
        check_size_usd=36_000,
        equity_pct=0.05,
        focus=["enterprise", "saas", "b2b"],
        location="San Francisco, CA",
        website="https://alchemistaccelerator.com",
        company_selector=".portfolio-item, .company-card, article",
    ),
    AcceleratorProgram(
        name="Dreamit Ventures",
        slug="dreamit-ventures",
        portfolio_url="https://www.dreamit.com/portfolio",
        check_size_usd=50_000,
        equity_pct=0.06,
        focus=["healthtech", "urbantech", "saas"],
        location="Philadelphia, PA",
        website="https://dreamit.com",
        company_selector=".portfolio-card, .company, article",
    ),
    AcceleratorProgram(
        name="Entrepreneurs Roundtable Accelerator",
        slug="era-accelerator",
        portfolio_url="https://www.eranyc.com/companies",
        check_size_usd=100_000,
        equity_pct=0.08,
        focus=["generalist"],
        location="New York, NY",
        website="https://eranyc.com",
        company_selector=".company-card, .portfolio-item, article",
    ),
    AcceleratorProgram(
        name="Techstars",
        slug="techstars",
        portfolio_url="https://www.techstars.com/portfolio",
        check_size_usd=120_000,
        equity_pct=0.06,
        focus=["generalist"],
        location="Boulder, CO",
        website="https://techstars.com",
        company_selector=".portfolio-card, .company-card, article",
    ),
    AcceleratorProgram(
        name="SOSV",
        slug="sosv",
        portfolio_url="https://sosv.com/portfolio/",
        check_size_usd=150_000,
        equity_pct=0.07,
        focus=["hardware", "biotech", "fintech"],
        location="Princeton, NJ",
        website="https://sosv.com",
        company_selector=".portfolio-card, .company-item, article",
    ),
    AcceleratorProgram(
        name="Founder Institute",
        slug="founder-institute",
        portfolio_url="https://fi.co/graduates",
        check_size_usd=0,  # program fee model, not investment
        equity_pct=0.04,
        focus=["generalist"],
        location="Silicon Valley, CA",
        website="https://fi.co",
        company_selector=".graduate-card, .company-card, article",
    ),
    AcceleratorProgram(
        name="StartX",
        slug="startx",
        portfolio_url="https://startx.com/companies",
        check_size_usd=0,  # Stanford-affiliated, no cash
        equity_pct=0.0,
        focus=["generalist"],
        location="Palo Alto, CA",
        website="https://startx.com",
        company_selector=".company-card, article",
    ),
    AcceleratorProgram(
        name="Gener8tor",
        slug="gener8tor",
        portfolio_url="https://www.gener8tor.com/portfolio",
        check_size_usd=100_000,
        equity_pct=0.07,
        focus=["generalist"],
        location="Milwaukee, WI",
        website="https://gener8tor.com",
        company_selector=".portfolio-item, .company-card, article",
    ),
    AcceleratorProgram(
        name="Indie Bio",
        slug="indie-bio",
        portfolio_url="https://indiebio.co/companies/",
        check_size_usd=250_000,
        equity_pct=0.08,
        focus=["biotech", "healthtech"],
        location="San Francisco, CA",
        website="https://indiebio.co",
        company_selector=".company-card, .portfolio-item, article",
    ),
    AcceleratorProgram(
        name="HAX",
        slug="hax",
        portfolio_url="https://hax.co/companies/",
        check_size_usd=250_000,
        equity_pct=0.08,
        focus=["hardware", "robotics", "climate"],
        location="Shenzhen / Newark",
        website="https://hax.co",
        company_selector=".company-card, .portfolio-item, article",
    ),
    AcceleratorProgram(
        name="On Deck",
        slug="on-deck",
        portfolio_url="https://www.beondeck.com/companies",
        check_size_usd=125_000,
        equity_pct=0.01,
        focus=["generalist"],
        location="San Francisco, CA",
        website="https://beondeck.com",
        company_selector=".company-card, article",
    ),
    AcceleratorProgram(
        name="South Park Commons",
        slug="south-park-commons",
        portfolio_url="https://www.southparkcommons.com/community",
        check_size_usd=0,
        equity_pct=0.0,
        focus=["deep_tech", "ai"],
        location="San Francisco, CA",
        website="https://southparkcommons.com",
        company_selector=".member-card, .company-card, article",
    ),
    AcceleratorProgram(
        name="Pioneer",
        slug="pioneer",
        portfolio_url="https://pioneer.app/winners",
        check_size_usd=20_000,
        equity_pct=0.01,
        focus=["generalist"],
        location="Remote",
        website="https://pioneer.app",
        company_selector=".winner-card, .company-card, article",
    ),
    AcceleratorProgram(
        name="Creative Destruction Lab",
        slug="creative-destruction-lab",
        portfolio_url="https://creativedestructionlab.com/companies/",
        check_size_usd=0,  # mentorship, no cash
        equity_pct=0.0,
        focus=["deep_tech", "ai", "quantum"],
        location="Toronto, Canada",
        website="https://creativedestructionlab.com",
        company_selector=".company-card, .portfolio-item, article",
    ),
    AcceleratorProgram(
        name="Betaworks",
        slug="betaworks",
        portfolio_url="https://www.betaworks.com/companies",
        check_size_usd=250_000,
        equity_pct=0.06,
        focus=["ai", "media", "consumer"],
        location="New York, NY",
        website="https://betaworks.com",
        company_selector=".company-card, .portfolio-item, article",
    ),
    AcceleratorProgram(
        name="Pear VC",
        slug="pear-vc",
        portfolio_url="https://pear.vc/companies/",
        check_size_usd=250_000,
        equity_pct=None,
        focus=["generalist"],
        location="Palo Alto, CA",
        website="https://pear.vc",
        company_selector=".company-card, .portfolio-item, article",
    ),
    AcceleratorProgram(
        name="Berkeley SkyDeck",
        slug="berkeley-skydeck",
        portfolio_url="https://skydeck.berkeley.edu/startups/",
        check_size_usd=100_000,
        equity_pct=0.05,
        focus=["generalist"],
        location="Berkeley, CA",
        website="https://skydeck.berkeley.edu",
        company_selector=".startup-card, .company-card, article",
    ),
    AcceleratorProgram(
        name="Newchip Accelerator",
        slug="newchip",
        portfolio_url="https://launch.newchip.com/portfolio",
        check_size_usd=0,  # mentorship model
        equity_pct=0.0,
        focus=["generalist"],
        location="Austin, TX",
        website="https://newchip.com",
        company_selector=".company-card, .portfolio-item, article",
    ),
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


class AcceleratorDirectoryCollector(BaseCollector):
    """Scrape portfolio pages from 20+ accelerator programs."""

    def source_type(self) -> str:
        return "accelerator_directory"

    async def collect(self) -> list[RawRound]:
        all_rounds: list[RawRound] = []

        async with httpx.AsyncClient(
            timeout=30.0,
            headers=HEADERS,
            follow_redirects=True,
        ) as client:
            for program in ACCELERATORS:
                try:
                    rounds = await self._scrape_program(client, program)
                    all_rounds.extend(rounds)
                    logger.info(f"[accel_dir] {program.name}: {len(rounds)} companies")
                except Exception as e:
                    logger.warning(f"[accel_dir] {program.name} failed: {e}")

                await asyncio.sleep(REQUEST_DELAY)

        logger.info(
            f"[accel_dir] Total: {len(all_rounds)} companies from {len(ACCELERATORS)} programs"
        )
        return all_rounds

    async def _scrape_program(
        self, client: httpx.AsyncClient, program: AcceleratorProgram
    ) -> list[RawRound]:
        """Scrape a single accelerator's portfolio page."""
        rounds: list[RawRound] = []
        seen_names: set[str] = set()

        try:
            resp = await client.get(program.portfolio_url)
            if resp.status_code != 200:
                logger.warning(f"[accel_dir] {program.name} returned {resp.status_code}")
                return rounds
        except Exception as e:
            logger.warning(f"[accel_dir] {program.name} fetch error: {e}")
            return rounds

        soup = BeautifulSoup(resp.text, "html.parser")
        companies = self._extract_companies(soup, program)

        for company in companies:
            name = company.get("name", "").strip()
            if not name or name.lower() in seen_names:
                continue
            if len(name) < 2 or len(name) > 200:
                continue

            seen_names.add(name.lower())
            raw = self._to_raw_round(company, program)
            if raw:
                rounds.append(raw)

            if len(rounds) >= MAX_COMPANIES_PER_PROGRAM:
                break

        return rounds

    def _extract_companies(self, soup: BeautifulSoup, program: AcceleratorProgram) -> list[dict]:
        """Extract company data from HTML using multiple strategies."""
        companies: list[dict] = []

        # Strategy 1: Use configured selectors
        cards = soup.select(program.company_selector)
        for card in cards:
            company = self._parse_card(card, program)
            if company and company.get("name"):
                companies.append(company)

        # Strategy 2: If few results, try common portfolio patterns
        if len(companies) < 5:
            companies.extend(self._fallback_extract(soup, program))

        # Strategy 3: Look for JSON-LD or Next.js data
        if len(companies) < 5:
            companies.extend(self._extract_from_json(soup, program))

        return companies

    def _parse_card(self, card, program: AcceleratorProgram) -> dict | None:
        """Parse a single company card element."""
        result = {}

        # Get name
        if program.name_selector:
            name_el = card.select_one(program.name_selector)
            if name_el:
                result["name"] = name_el.get_text(strip=True)
        if not result.get("name"):
            # Try heading tags
            for tag in ["h2", "h3", "h4", "h5", "strong", "b"]:
                el = card.find(tag)
                if el:
                    text = el.get_text(strip=True)
                    if 2 < len(text) < 100:
                        result["name"] = text
                        break
        if not result.get("name"):
            # Try the card's own text or aria-label
            label = card.get("aria-label", "")
            if label:
                result["name"] = label
            elif card.name == "a":
                text = card.get_text(strip=True)
                if 2 < len(text) < 100:
                    result["name"] = text

        if not result.get("name"):
            return None

        # Get description
        if program.desc_selector:
            desc_el = card.select_one(program.desc_selector)
            if desc_el:
                result["description"] = desc_el.get_text(strip=True)
        if not result.get("description"):
            for selector in ["p", ".description", ".desc", ".blurb", "[class*='desc']"]:
                el = card.select_one(selector)
                if el:
                    text = el.get_text(strip=True)
                    if len(text) > 10:
                        result["description"] = text[:500]
                        break

        # Get website URL
        if card.name == "a" and card.get("href"):
            href = card["href"]
            if href.startswith("http") and program.slug not in href:
                result["website"] = href
        else:
            link = card.find("a", href=True)
            if link:
                href = link["href"]
                if href.startswith("http"):
                    result["website"] = href

        # Get image (logo URL)
        img = card.find("img")
        if img and img.get("alt"):
            alt = img["alt"].strip()
            if not result.get("name") and 2 < len(alt) < 100:
                result["name"] = alt

        return result

    def _fallback_extract(self, soup: BeautifulSoup, program: AcceleratorProgram) -> list[dict]:
        """Fallback: look for common portfolio page patterns."""
        companies = []

        # Pattern: grid of linked logos/cards
        for container_sel in [
            ".portfolio",
            ".companies",
            ".startups",
            ".alumni",
            "[class*='portfolio']",
            "[class*='company']",
            "[class*='startup']",
            "#portfolio",
            "#companies",
        ]:
            container = soup.select_one(container_sel)
            if not container:
                continue

            for el in container.find_all(["a", "div", "li"], recursive=True):
                name = None
                # Try heading first
                heading = el.find(["h2", "h3", "h4", "h5"])
                if heading:
                    name = heading.get_text(strip=True)
                elif el.name == "a":
                    name = el.get_text(strip=True)
                    if not name:
                        img = el.find("img")
                        if img:
                            name = img.get("alt", "").strip()

                if name and 2 < len(name) < 100:
                    website = None
                    if el.name == "a" and el.get("href", "").startswith("http"):
                        href = el["href"]
                        if program.slug not in href:
                            website = href

                    companies.append(
                        {
                            "name": name,
                            "website": website,
                        }
                    )

            if companies:
                break

        return companies

    def _extract_from_json(self, soup: BeautifulSoup, program: AcceleratorProgram) -> list[dict]:
        """Extract from embedded JSON (Next.js __NEXT_DATA__, JSON-LD, etc.)."""
        companies = []

        # Next.js data
        script = soup.find("script", id="__NEXT_DATA__")
        if script and script.string:
            try:
                import json

                data = json.loads(script.string)
                companies.extend(self._walk_json_for_companies(data, program))
            except Exception:
                pass

        # JSON-LD
        for script in soup.find_all("script", type="application/ld+json"):
            if script.string:
                try:
                    import json

                    data = json.loads(script.string)
                    if isinstance(data, dict) and data.get("@type") == "ItemList":
                        for item in data.get("itemListElement", []):
                            name = item.get("name", "")
                            if name:
                                companies.append(
                                    {
                                        "name": name,
                                        "website": item.get("url"),
                                        "description": item.get("description"),
                                    }
                                )
                except Exception:
                    pass

        return companies

    def _walk_json_for_companies(
        self, data, program: AcceleratorProgram, depth: int = 0
    ) -> list[dict]:
        """Recursively walk JSON looking for arrays of company-like objects."""
        if depth > 8:
            return []

        companies = []

        if isinstance(data, list) and len(data) > 3:
            # Check if this looks like a company list
            sample = data[0] if data else {}
            if isinstance(sample, dict) and any(
                k in sample for k in ["name", "company_name", "startup_name", "title"]
            ):
                for item in data:
                    if not isinstance(item, dict):
                        continue
                    name = (
                        item.get("name")
                        or item.get("company_name")
                        or item.get("startup_name")
                        or item.get("title", "")
                    ).strip()
                    if name and 2 < len(name) < 100:
                        companies.append(
                            {
                                "name": name,
                                "website": item.get("website") or item.get("url"),
                                "description": (
                                    item.get("description")
                                    or item.get("blurb")
                                    or item.get("one_liner")
                                ),
                                "location": item.get("location") or item.get("city"),
                                "batch": item.get("batch") or item.get("cohort"),
                            }
                        )
                if companies:
                    return companies

        if isinstance(data, dict):
            for val in data.values():
                if isinstance(val, (dict, list)):
                    found = self._walk_json_for_companies(val, program, depth + 1)
                    if found:
                        return found
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, (dict, list)):
                    found = self._walk_json_for_companies(item, program, depth + 1)
                    if found:
                        return found

        return companies

    def _to_raw_round(self, company: dict, program: AcceleratorProgram) -> RawRound | None:
        """Convert extracted company data to RawRound."""
        name = company.get("name", "").strip()
        if not name:
            return None

        # Skip navigation/menu items
        skip_words = {
            "home",
            "about",
            "contact",
            "portfolio",
            "apply",
            "blog",
            "team",
            "news",
            "press",
            "careers",
            "privacy",
            "terms",
            "login",
            "sign up",
            "menu",
            "close",
            "search",
        }
        if name.lower() in skip_words:
            return None

        batch = company.get("batch", "")
        round_date = date.today()
        if batch:
            year_match = re.search(r"20\d{2}", str(batch))
            if year_match:
                try:
                    round_date = date(int(year_match.group()), 6, 1)
                except ValueError:
                    pass

        raw_data = {
            "accelerator": program.name,
            "accelerator_batch": batch or None,
            "one_liner": company.get("description"),
            "location": company.get("location"),
            "website": company.get("website"),
            "source": "accelerator_directory",
            "program_check_size": program.check_size_usd,
            "program_equity_pct": program.equity_pct,
            "program_focus": program.focus,
            "program_location": program.location,
        }
        # Remove None values
        raw_data = {k: v for k, v in raw_data.items() if v is not None}

        return RawRound(
            project_name=name,
            date=round_date,
            round_type="accelerator",
            amount_usd=program.check_size_usd if program.check_size_usd > 0 else None,
            lead_investors=[program.name],
            project_url=company.get("website"),
            raw_data=raw_data,
        )
