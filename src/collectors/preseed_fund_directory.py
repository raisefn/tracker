"""Pre-seed and micro-VC fund directory.

Creates Investor records for active pre-seed funds and micro-VCs that
are too small to appear in SEC 13F filings but are the most relevant
investors for pre-seed founders.

This is an enricher (not a collector) because it creates Investor records
directly rather than producing RawRound objects.

Sources:
- Curated list of known active pre-seed/micro-VC funds
- Web scraping of each fund's website for portfolio/team/thesis data
"""

import asyncio
import logging
from datetime import datetime, timezone

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.collectors.enrichment_base import BaseEnricher, EnrichmentResult, stamp_freshness
from src.models import Investor
from src.pipeline.normalizer import make_slug

logger = logging.getLogger(__name__)

SOURCE_KEY = "preseed_fund_directory"
REQUEST_DELAY = 2

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


# ── Pre-seed and micro-VC fund directory ───────────────────────────────
# Curated from public sources: Crunchbase, AngelList, fund announcements.
# Each entry includes enough metadata to create a useful Investor record
# even if the website scrape fails.

PRESEED_FUNDS: list[dict] = [
    # Top pre-seed / micro-VCs
    {
        "name": "Precursor Ventures",
        "website": "https://precursorvc.com",
        "twitter": "@precaborVC",
        "location": "San Francisco, CA",
        "focus": ["generalist"],
        "check_range": "$100K-$500K",
        "type": "vc",
    },
    {
        "name": "Hustle Fund",
        "website": "https://www.hustlefund.vc",
        "twitter": "@hustlefundvc",
        "location": "San Francisco, CA",
        "focus": ["generalist"],
        "check_range": "$25K-$250K",
        "type": "vc",
    },
    {
        "name": "1517 Fund",
        "website": "https://www.1517fund.com",
        "twitter": "@1517fund",
        "location": "San Francisco, CA",
        "focus": ["deep_tech", "education"],
        "check_range": "$100K-$500K",
        "type": "vc",
    },
    {
        "name": "Soma Capital",
        "website": "https://somacap.com",
        "twitter": "@SomaCapital",
        "location": "San Francisco, CA",
        "focus": ["generalist"],
        "check_range": "$50K-$250K",
        "type": "vc",
    },
    {
        "name": "Chapter One",
        "website": "https://chapterone.com",
        "twitter": "@chaborone",
        "location": "San Francisco, CA",
        "focus": ["consumer", "crypto"],
        "check_range": "$500K-$2M",
        "type": "vc",
    },
    {
        "name": "Afore Capital",
        "website": "https://afore.vc",
        "twitter": "@AforeCapital",
        "location": "San Francisco, CA",
        "focus": ["generalist"],
        "check_range": "$500K-$1.5M",
        "type": "vc",
    },
    {
        "name": "Weekend Fund",
        "website": "https://www.weekend.fund",
        "twitter": "@weekendfund",
        "location": "San Francisco, CA",
        "focus": ["consumer", "saas"],
        "check_range": "$100K-$500K",
        "type": "vc",
    },
    {
        "name": "Unpopular Ventures",
        "website": "https://unpopular.vc",
        "twitter": "@unpopularvc",
        "location": "Remote",
        "focus": ["generalist"],
        "check_range": "$50K-$200K",
        "type": "vc",
    },
    {
        "name": "Calm Ventures",
        "website": "https://calmfund.com",
        "twitter": "@calmfund",
        "location": "Remote",
        "focus": ["saas", "bootstrapped"],
        "check_range": "$100K-$500K",
        "type": "vc",
    },
    {
        "name": "Magic Fund",
        "website": "https://www.magicfund.co",
        "twitter": None,
        "location": "San Francisco, CA",
        "focus": ["generalist"],
        "check_range": "$50K-$150K",
        "type": "vc",
    },
    {
        "name": "Everywhere Ventures",
        "website": "https://everywhere.vc",
        "twitter": "@everywherevc",
        "location": "New York, NY",
        "focus": ["generalist"],
        "check_range": "$50K-$250K",
        "type": "vc",
    },
    {
        "name": "Indie.vc",
        "website": "https://www.indie.vc",
        "twitter": "@indievc",
        "location": "Portland, OR",
        "focus": ["sustainable", "bootstrapped"],
        "check_range": "$100K-$500K",
        "type": "vc",
    },
    {
        "name": "Tiny Seed",
        "website": "https://tinyseed.com",
        "twitter": "@TinySeed",
        "location": "Minneapolis, MN",
        "focus": ["saas", "bootstrapped"],
        "check_range": "$120K-$250K",
        "type": "vc",
    },
    {
        "name": "SaaStr Fund",
        "website": "https://saastr.fund",
        "twitter": "@saastr",
        "location": "San Francisco, CA",
        "focus": ["saas"],
        "check_range": "$100K-$500K",
        "type": "vc",
    },
    {
        "name": "Backstage Capital",
        "website": "https://backstagecapital.com",
        "twitter": "@backstaborCap",
        "location": "Los Angeles, CA",
        "focus": ["generalist", "underrepresented"],
        "check_range": "$25K-$100K",
        "type": "vc",
    },
    {
        "name": "Lerer Hippeau",
        "website": "https://lererhippeau.com",
        "twitter": "@lererhippeau",
        "location": "New York, NY",
        "focus": ["consumer", "marketplace"],
        "check_range": "$250K-$1M",
        "type": "vc",
    },
    {
        "name": "Notation Capital",
        "website": "https://notation.vc",
        "twitter": "@NotationCapital",
        "location": "Brooklyn, NY",
        "focus": ["generalist"],
        "check_range": "$250K-$1M",
        "type": "vc",
    },
    {
        "name": "Boldstart Ventures",
        "website": "https://boldstart.vc",
        "twitter": "@BoldstartVC",
        "location": "New York, NY",
        "focus": ["enterprise", "developer_tools"],
        "check_range": "$500K-$3M",
        "type": "vc",
    },
    {
        "name": "Haystack",
        "website": "https://haystack.vc",
        "twitter": "@semil",
        "location": "San Francisco, CA",
        "focus": ["generalist"],
        "check_range": "$100K-$500K",
        "type": "vc",
    },
    {
        "name": "Asymmetric Capital Partners",
        "website": "https://acp.vc",
        "twitter": None,
        "location": "San Francisco, CA",
        "focus": ["fintech", "saas"],
        "check_range": "$250K-$1M",
        "type": "vc",
    },
    {
        "name": "Wischoff Ventures",
        "website": "https://wischoff.com",
        "twitter": "@nickwischoff",
        "location": "San Francisco, CA",
        "focus": ["saas"],
        "check_range": "$25K-$100K",
        "type": "vc",
    },
    {
        "name": "Banana Capital",
        "website": "https://www.banana.vc",
        "twitter": "@bananacapital",
        "location": "San Francisco, CA",
        "focus": ["generalist"],
        "check_range": "$25K-$100K",
        "type": "vc",
    },
    # Sector-specific pre-seed funds
    {
        "name": "Betaworks",
        "website": "https://betaworks.com",
        "twitter": "@betaworks",
        "location": "New York, NY",
        "focus": ["ai", "media"],
        "check_range": "$250K-$1M",
        "type": "vc",
    },
    {
        "name": "Gradient Ventures",
        "website": "https://gradient.google",
        "twitter": None,
        "location": "San Francisco, CA",
        "focus": ["ai"],
        "check_range": "$500K-$3M",
        "type": "vc",
    },
    {
        "name": "Air Street Capital",
        "website": "https://www.airstreet.com",
        "twitter": "@airaboreet",
        "location": "London, UK",
        "focus": ["ai"],
        "check_range": "$250K-$2M",
        "type": "vc",
    },
    {
        "name": "Kindred Ventures",
        "website": "https://kindredventures.com",
        "twitter": "@kindredv",
        "location": "San Francisco, CA",
        "focus": ["fintech", "crypto"],
        "check_range": "$250K-$1M",
        "type": "vc",
    },
    {
        "name": "Operator Partners",
        "website": "https://www.operator.partners",
        "twitter": None,
        "location": "San Francisco, CA",
        "focus": ["saas", "fintech"],
        "check_range": "$100K-$500K",
        "type": "vc",
    },
    # Active international pre-seed funds
    {
        "name": "Seedcamp",
        "website": "https://seedcamp.com",
        "twitter": "@seedcamp",
        "location": "London, UK",
        "focus": ["generalist"],
        "check_range": "$100K-$500K",
        "type": "vc",
    },
    {
        "name": "Antler",
        "website": "https://antler.co",
        "twitter": "@AntlerGlobal",
        "location": "Singapore",
        "focus": ["generalist"],
        "check_range": "$100K-$250K",
        "type": "vc",
    },
    {
        "name": "Entrepreneur First",
        "website": "https://www.joinef.com",
        "twitter": "@join_ef",
        "location": "London, UK",
        "focus": ["deep_tech"],
        "check_range": "$80K-$250K",
        "type": "vc",
    },
    {
        "name": "Lunar Ventures",
        "website": "https://lunar.vc",
        "twitter": "@lunarvc",
        "location": "Berlin, Germany",
        "focus": ["deep_tech", "ai"],
        "check_range": "$200K-$1M",
        "type": "vc",
    },
    {
        "name": "Kima Ventures",
        "website": "https://www.kimaventures.com",
        "twitter": "@kimaventures",
        "location": "Paris, France",
        "focus": ["generalist"],
        "check_range": "$50K-$200K",
        "type": "vc",
    },
    # Crypto/Web3 pre-seed
    {
        "name": "Alliance DAO",
        "website": "https://alliance.xyz",
        "twitter": "@AllianceDAO",
        "location": "Remote",
        "focus": ["crypto", "web3"],
        "check_range": "$100K-$500K",
        "type": "dao",
    },
    {
        "name": "Seed Club Ventures",
        "website": "https://seedclub.xyz",
        "twitter": "@seedclubhq",
        "location": "Remote",
        "focus": ["crypto", "web3", "social"],
        "check_range": "$50K-$250K",
        "type": "dao",
    },
    {
        "name": "Bain Capital Crypto",
        "website": "https://www.baincapitalcrypto.com",
        "twitter": "@BainCapCrypto",
        "location": "Boston, MA",
        "focus": ["crypto"],
        "check_range": "$500K-$5M",
        "type": "vc",
    },
    {
        "name": "1kx",
        "website": "https://1kx.network",
        "twitter": "@1kabornetwork",
        "location": "Remote",
        "focus": ["crypto", "defi"],
        "check_range": "$250K-$2M",
        "type": "vc",
    },
    {
        "name": "Variant",
        "website": "https://variant.fund",
        "twitter": "@variantfund",
        "location": "New York, NY",
        "focus": ["crypto", "web3"],
        "check_range": "$500K-$5M",
        "type": "vc",
    },
    # Climate / Impact pre-seed
    {
        "name": "Congruent Ventures",
        "website": "https://congruentvc.com",
        "twitter": "@congruentvc",
        "location": "San Francisco, CA",
        "focus": ["climate", "sustainability"],
        "check_range": "$250K-$2M",
        "type": "vc",
    },
    {
        "name": "Lowercarbon Capital",
        "website": "https://lowercarboncapital.com",
        "twitter": "@lcaborital",
        "location": "San Francisco, CA",
        "focus": ["climate"],
        "check_range": "$250K-$2M",
        "type": "vc",
    },
    {
        "name": "MCJ Collective",
        "website": "https://www.mcjcollective.com",
        "twitter": "@MCJCollective",
        "location": "San Francisco, CA",
        "focus": ["climate"],
        "check_range": "$100K-$500K",
        "type": "vc",
    },
    # Health / Bio pre-seed
    {
        "name": "Pear Bio",
        "website": "https://www.pearbio.com",
        "twitter": None,
        "location": "San Francisco, CA",
        "focus": ["biotech", "healthtech"],
        "check_range": "$250K-$2M",
        "type": "vc",
    },
    {
        "name": "Fifty Years",
        "website": "https://www.fiftyyears.com",
        "twitter": "@FiftyYearsVC",
        "location": "San Francisco, CA",
        "focus": ["deep_tech", "science"],
        "check_range": "$250K-$1M",
        "type": "vc",
    },
    # Fintech pre-seed
    {
        "name": "Ribbit Capital",
        "website": "https://ribbitcap.com",
        "twitter": "@RibbitCapital",
        "location": "Palo Alto, CA",
        "focus": ["fintech"],
        "check_range": "$500K-$5M",
        "type": "vc",
    },
    {
        "name": "QED Investors",
        "website": "https://qedinvestors.com",
        "twitter": "@QEDInvestors",
        "location": "Alexandria, VA",
        "focus": ["fintech"],
        "check_range": "$500K-$5M",
        "type": "vc",
    },
    {
        "name": "Nyca Partners",
        "website": "https://www.nycapartners.com",
        "twitter": "@nycapartners",
        "location": "New York, NY",
        "focus": ["fintech"],
        "check_range": "$500K-$3M",
        "type": "vc",
    },
]


class PreSeedFundDirectory(BaseEnricher):
    """Create/update Investor records for known pre-seed and micro-VC funds."""

    def source_name(self) -> str:
        return SOURCE_KEY

    async def enrich(self, session: AsyncSession) -> EnrichmentResult:
        result = EnrichmentResult(source=self.source_name())

        async with httpx.AsyncClient(
            timeout=15,
            headers=HEADERS,
            follow_redirects=True,
        ) as client:
            for fund in PRESEED_FUNDS:
                try:
                    created = await self._upsert_fund(session, client, fund)
                    if created:
                        result.records_updated += 1
                    else:
                        result.records_skipped += 1
                except Exception as e:
                    error_msg = f"{fund['name']}: {e}"
                    logger.warning(f"[preseed_dir] Error: {error_msg}")
                    result.errors.append(error_msg)

                await asyncio.sleep(0.5)  # light delay for DB, not scraping every fund

        await session.flush()
        logger.info(
            f"[preseed_dir] Done: {result.records_updated} created/updated, "
            f"{result.records_skipped} skipped"
        )
        return result

    async def _upsert_fund(
        self,
        session: AsyncSession,
        client: httpx.AsyncClient,
        fund: dict,
    ) -> bool:
        """Create or update an Investor record for a pre-seed fund."""
        slug = make_slug(fund["name"])

        # Check if already exists
        result = await session.execute(select(Investor).where(Investor.slug == slug))
        investor = result.scalar_one_or_none()

        if investor:
            # Update only if missing data
            updated = False
            if not investor.website and fund.get("website"):
                investor.website = fund["website"]
                updated = True
            if not investor.twitter and fund.get("twitter"):
                investor.twitter = fund["twitter"]
                updated = True
            if not investor.hq_location and fund.get("location"):
                investor.hq_location = fund["location"]
                updated = True
            if not investor.type:
                investor.type = fund.get("type", "vc")
                updated = True
            if not investor.investor_category:
                investor.investor_category = "pre_seed_fund"
                updated = True

            # Try to scrape description if missing
            if not investor.description and fund.get("website"):
                desc = await self._scrape_description(client, fund["website"])
                if desc:
                    investor.description = desc
                    updated = True

            if updated:
                stamp_freshness(investor, self.source_name())
                investor.last_enriched_at = datetime.now(timezone.utc)
            return updated

        # Create new investor
        description = None
        if fund.get("website"):
            description = await self._scrape_description(client, fund["website"])
            await asyncio.sleep(REQUEST_DELAY)

        investor = Investor(
            name=fund["name"],
            slug=slug,
            type=fund.get("type", "vc"),
            website=fund.get("website"),
            twitter=fund.get("twitter"),
            description=description,
            hq_location=fund.get("location"),
            investor_category="pre_seed_fund",
            source_freshness={SOURCE_KEY: datetime.now(timezone.utc).isoformat()},
            last_enriched_at=datetime.now(timezone.utc),
        )
        session.add(investor)
        return True

    async def _scrape_description(self, client: httpx.AsyncClient, url: str) -> str | None:
        """Try to scrape a short description from the fund's website."""
        try:
            resp = await client.get(url)
            if resp.status_code != 200:
                return None

            soup = BeautifulSoup(resp.text, "html.parser")

            # Try meta description first
            meta = soup.find("meta", attrs={"name": "description"})
            if meta and meta.get("content"):
                desc = meta["content"].strip()
                if len(desc) > 20:
                    return desc[:1000]

            # Try og:description
            meta = soup.find("meta", attrs={"property": "og:description"})
            if meta and meta.get("content"):
                desc = meta["content"].strip()
                if len(desc) > 20:
                    return desc[:1000]

            # Try first meaningful paragraph
            for p in soup.find_all("p"):
                text = p.get_text(strip=True)
                if len(text) > 50 and not any(
                    kw in text.lower() for kw in ["cookie", "privacy", "©", "copyright"]
                ):
                    return text[:1000]

        except Exception:
            pass

        return None
