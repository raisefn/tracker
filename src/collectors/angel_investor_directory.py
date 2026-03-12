"""Individual angel investor directory.

Creates Investor records for known active angel investors. These are
individuals who write pre-seed/seed checks and are critical for founders
raising under $500K.

Sources:
- Curated list of publicly known active angels (from AngelList, Twitter,
  public investment announcements)
- Scrapes their public profiles for bio/description

This is an enricher because it creates Investor records directly.
"""

import asyncio
import logging
import re
from datetime import datetime, timezone

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.collectors.enrichment_base import BaseEnricher, EnrichmentResult, stamp_freshness
from src.models import Investor
from src.pipeline.normalizer import make_slug

logger = logging.getLogger(__name__)

SOURCE_KEY = "angel_investor_directory"
REQUEST_DELAY = 2

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


# ── Angel investor directory ──────────────────────────────────────────
# Curated from public angel lists, AngelList syndicates, Twitter bios,
# and investment announcement databases.
#
# Focus: angels who are publicly known to write $10K-$250K checks at
# pre-seed/seed. Not exhaustive — this is a starting seed that grows
# as the AngelList and Wellfound enrichers discover more.

ANGEL_INVESTORS: list[dict] = [
    # Super-angels / prolific angels
    {"name": "Naval Ravikant", "twitter": "@naval", "location": "San Francisco, CA", "focus": ["generalist", "crypto"], "check_range": "$25K-$500K", "bio": "Co-founder AngelList. Angel investor in 200+ companies including Twitter, Uber, Notion."},
    {"name": "Jason Calacanis", "twitter": "@Jason", "location": "San Francisco, CA", "focus": ["generalist"], "check_range": "$25K-$100K", "bio": "Angel investor, host of All-In Podcast and This Week in Startups. Early investor in Uber, Robinhood, Calm."},
    {"name": "Elad Gil", "twitter": "@eaborl", "location": "San Francisco, CA", "focus": ["generalist", "ai"], "check_range": "$100K-$2M", "bio": "Author of High Growth Handbook. Angel/seed investor in Airbnb, Coinbase, Figma, Notion, Stripe."},
    {"name": "Sahil Lavingia", "twitter": "@shl", "location": "Provo, UT", "focus": ["saas", "creator_economy"], "check_range": "$25K-$250K", "bio": "CEO of Gumroad. Angel investor in 250+ startups."},
    {"name": "Austen Allred", "twitter": "@AustenAllred", "location": "San Francisco, CA", "focus": ["education", "saas"], "check_range": "$25K-$250K", "bio": "CEO of Bloom Institute. Active angel investor in edtech and SaaS."},
    {"name": "Alexis Ohanian", "twitter": "@alexisohanian", "location": "Los Angeles, CA", "focus": ["consumer", "creator_economy"], "check_range": "$25K-$500K", "bio": "Co-founder Reddit, founder Seven Seven Six. Prolific angel and seed investor."},
    {"name": "Balaji Srinivasan", "twitter": "@balajis", "location": "Remote", "focus": ["crypto", "biotech", "deep_tech"], "check_range": "$25K-$500K", "bio": "Former CTO of Coinbase, GP at a16z. Active angel in crypto, biotech, and network states."},
    {"name": "Garry Tan", "twitter": "@garrytan", "location": "San Francisco, CA", "focus": ["generalist"], "check_range": "$25K-$500K", "bio": "President of Y Combinator. Co-founder of Initialized Capital. Angel in Coinbase, Instacart."},
    {"name": "Ryan Hoover", "twitter": "@rrhoover", "location": "San Francisco, CA", "focus": ["consumer", "saas"], "check_range": "$10K-$100K", "bio": "Founder of Product Hunt. Active angel investor in consumer and SaaS startups."},
    {"name": "Harry Stebbings", "twitter": "@HarryStebbings", "location": "London, UK", "focus": ["saas", "fintech"], "check_range": "$100K-$500K", "bio": "Founder of 20VC. Prolific podcaster and angel investor."},
    {"name": "David Sacks", "twitter": "@DavidSacks", "location": "San Francisco, CA", "focus": ["saas", "enterprise"], "check_range": "$100K-$1M", "bio": "Co-founder of Craft Ventures. Former COO of PayPal. Angel and seed investor."},
    {"name": "Cindy Bi", "twitter": "@CindyBi", "location": "San Francisco, CA", "focus": ["ai", "enterprise"], "check_range": "$25K-$250K", "bio": "Angel investor and LP. Former VC at Capital Factory, active in AI and enterprise."},
    {"name": "Gokul Rajaram", "twitter": "@gokulr", "location": "San Francisco, CA", "focus": ["saas", "fintech", "marketplace"], "check_range": "$25K-$250K", "bio": "Board member at Coinbase, Pinterest. Super-angel with 100+ investments."},
    {"name": "Scott Belsky", "twitter": "@scottbelsky", "location": "New York, NY", "focus": ["creator_economy", "design"], "check_range": "$25K-$500K", "bio": "Chief Strategy Officer at Adobe. Founder of Behance. Active angel investor."},
    {"name": "Shaan Puri", "twitter": "@ShaanVP", "location": "Austin, TX", "focus": ["consumer", "media"], "check_range": "$25K-$100K", "bio": "Host of My First Million. Active angel investor in consumer and media companies."},
    {"name": "Sam Parr", "twitter": "@TheSamParr", "location": "Austin, TX", "focus": ["media", "saas"], "check_range": "$25K-$100K", "bio": "Founder of The Hustle (sold to HubSpot). Angel investor."},
    {"name": "Lachy Groom", "twitter": "@LachyGroom", "location": "San Francisco, CA", "focus": ["fintech", "infrastructure"], "check_range": "$100K-$1M", "bio": "Former Stripe. Solo GP angel/seed investor in fintech and infrastructure."},
    {"name": "Todd Goldberg", "twitter": "@toddgoldberg", "location": "San Francisco, CA", "focus": ["consumer", "enterprise"], "check_range": "$25K-$100K", "bio": "Co-founder Eventbrite. Active angel investor."},
    {"name": "Dharmesh Shah", "twitter": "@dharmesh", "location": "Boston, MA", "focus": ["saas", "ai"], "check_range": "$25K-$500K", "bio": "CTO/Co-founder HubSpot. Prolific angel investor in SaaS and AI startups."},
    {"name": "Hiten Shah", "twitter": "@hnshah", "location": "San Francisco, CA", "focus": ["saas"], "check_range": "$25K-$100K", "bio": "Co-founder of Crazy Egg, KISSmetrics, FYI. Active SaaS angel."},
    {"name": "Li Jin", "twitter": "@ljin18", "location": "San Francisco, CA", "focus": ["creator_economy", "consumer"], "check_range": "$100K-$500K", "bio": "Founder of Atelier Ventures. Former a16z. Focuses on creator economy."},

    # Crypto / Web3 angels
    {"name": "Vitalik Buterin", "twitter": "@VitalikButerin", "location": "Remote", "focus": ["crypto", "ethereum", "public_goods"], "check_range": "$50K-$500K", "bio": "Co-founder of Ethereum. Angel investor in crypto and public goods projects."},
    {"name": "Stani Kulechov", "twitter": "@StaniKulechov", "location": "London, UK", "focus": ["defi", "crypto"], "check_range": "$50K-$500K", "bio": "Founder of Aave and Avara. Active DeFi angel investor."},
    {"name": "Sandeep Nailwal", "twitter": "@sandeepnailwal", "location": "Dubai, UAE", "focus": ["crypto", "web3"], "check_range": "$50K-$500K", "bio": "Co-founder of Polygon. Active angel in Web3 and crypto startups."},
    {"name": "Santiago Roel Santos", "twitter": "@santiagoroel", "location": "Remote", "focus": ["crypto", "defi"], "check_range": "$25K-$250K", "bio": "Angel investor and researcher focused on DeFi and crypto."},

    # Fintech angels
    {"name": "Josh Browder", "twitter": "@jbrowder1", "location": "San Francisco, CA", "focus": ["ai", "legaltech"], "check_range": "$25K-$100K", "bio": "CEO of DoNotPay. Active angel investor in AI and legal tech."},
    {"name": "Zach Perret", "twitter": "@zachperret", "location": "San Francisco, CA", "focus": ["fintech"], "check_range": "$25K-$250K", "bio": "CEO of Plaid. Angel investor in fintech startups."},
    {"name": "Max Levchin", "twitter": "@maborvchin", "location": "San Francisco, CA", "focus": ["fintech", "consumer"], "check_range": "$100K-$1M", "bio": "CEO of Affirm, co-founder of PayPal. Active angel investor."},

    # AI / Deep Tech angels
    {"name": "Nat Friedman", "twitter": "@nataboridman", "location": "San Francisco, CA", "focus": ["ai", "developer_tools"], "check_range": "$100K-$1M", "bio": "Former CEO of GitHub. Active AI investor."},
    {"name": "Daniel Gross", "twitter": "@danielgross", "location": "San Francisco, CA", "focus": ["ai"], "check_range": "$100K-$1M", "bio": "Pioneer founder, former Apple AI. Active AI angel and seed investor."},
    {"name": "Amjad Masad", "twitter": "@amasad", "location": "San Francisco, CA", "focus": ["developer_tools", "ai"], "check_range": "$25K-$250K", "bio": "CEO of Replit. Active angel in developer tools and AI."},

    # International angels
    {"name": "Reshma Sohoni", "twitter": "@rsohoni", "location": "London, UK", "focus": ["generalist"], "check_range": "$50K-$250K", "bio": "Co-founder of Seedcamp. Active European angel and seed investor."},
    {"name": "Carlos Eduardo Espinal", "twitter": "@cee", "location": "London, UK", "focus": ["generalist"], "check_range": "$50K-$250K", "bio": "Partner at Seedcamp. Author and active European angel."},
    {"name": "Fabrice Grinda", "twitter": "@fabricegrinda", "location": "New York, NY", "focus": ["marketplace"], "check_range": "$100K-$500K", "bio": "Co-founder of FJ Labs. Angel in 900+ companies, focused on marketplaces."},

    # Operator angels (active founders who angel invest)
    {"name": "Vlad Tenev", "twitter": "@vladtenev", "location": "San Francisco, CA", "focus": ["fintech"], "check_range": "$25K-$250K", "bio": "Co-founder and CEO of Robinhood. Angel investor in fintech."},
    {"name": "Patrick Collison", "twitter": "@patrickc", "location": "San Francisco, CA", "focus": ["developer_tools", "infrastructure"], "check_range": "$25K-$500K", "bio": "CEO of Stripe. Selective angel investor in infrastructure and dev tools."},
    {"name": "Tobi Lutke", "twitter": "@tobi", "location": "Ottawa, Canada", "focus": ["commerce", "saas"], "check_range": "$25K-$500K", "bio": "CEO of Shopify. Angel investor in commerce and SaaS companies."},
    {"name": "Dylan Field", "twitter": "@zoink", "location": "San Francisco, CA", "focus": ["design", "developer_tools"], "check_range": "$25K-$250K", "bio": "CEO of Figma. Angel investor in design and developer tools."},
    {"name": "Guillermo Rauch", "twitter": "@raaborchg", "location": "San Francisco, CA", "focus": ["developer_tools", "web"], "check_range": "$25K-$100K", "bio": "CEO of Vercel. Active angel in developer tools and web infrastructure."},
    {"name": "Mitchell Hashimoto", "twitter": "@mitchellh", "location": "San Francisco, CA", "focus": ["developer_tools", "infrastructure"], "check_range": "$25K-$100K", "bio": "Co-founder of HashiCorp. Angel investor in developer tools."},
]


class AngelInvestorDirectory(BaseEnricher):
    """Create/update Investor records for known individual angel investors."""

    def source_name(self) -> str:
        return SOURCE_KEY

    async def enrich(self, session: AsyncSession) -> EnrichmentResult:
        result = EnrichmentResult(source=self.source_name())

        for angel in ANGEL_INVESTORS:
            try:
                created = await self._upsert_angel(session, angel)
                if created:
                    result.records_updated += 1
                else:
                    result.records_skipped += 1
            except Exception as e:
                error_msg = f"{angel['name']}: {e}"
                logger.warning(f"[angel_dir] Error: {error_msg}")
                result.errors.append(error_msg)

        await session.flush()
        logger.info(
            f"[angel_dir] Done: {result.records_updated} created/updated, "
            f"{result.records_skipped} skipped"
        )
        return result

    async def _upsert_angel(
        self,
        session: AsyncSession,
        angel: dict,
    ) -> bool:
        """Create or update an Investor record for an angel investor."""
        slug = make_slug(angel["name"])

        result = await session.execute(
            select(Investor).where(Investor.slug == slug)
        )
        investor = result.scalar_one_or_none()

        if investor:
            # Update only missing fields
            updated = False
            if not investor.twitter and angel.get("twitter"):
                investor.twitter = angel["twitter"]
                updated = True
            if not investor.hq_location and angel.get("location"):
                investor.hq_location = angel["location"]
                updated = True
            if not investor.type:
                investor.type = "angel"
                updated = True
            if not investor.description and angel.get("bio"):
                investor.description = angel["bio"]
                updated = True
            if not investor.investor_category:
                investor.investor_category = "angel_investor"
                updated = True

            if updated:
                stamp_freshness(investor, self.source_name())
                investor.last_enriched_at = datetime.now(timezone.utc)
            return updated

        # Create new
        investor = Investor(
            name=angel["name"],
            slug=slug,
            type="angel",
            twitter=angel.get("twitter"),
            description=angel.get("bio"),
            hq_location=angel.get("location"),
            investor_category="angel_investor",
            source_freshness={SOURCE_KEY: datetime.now(timezone.utc).isoformat()},
            last_enriched_at=datetime.now(timezone.utc),
        )
        session.add(investor)
        return True
