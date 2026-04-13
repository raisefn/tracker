"""Latam VC Directory — curated seed data for Mexico, Brazil, and Colombia investors."""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.collectors.enrichment_base import BaseEnricher, EnrichmentResult
from src.models.investor import Investor

logger = logging.getLogger(__name__)


# Curated Latam VC data from public sources
LATAM_VCS = [
    # ── Brazil ──
    {
        "name": "Canary",
        "slug": "canary",
        "type": "vc",
        "hq_location": "São Paulo, Brazil",
        "website": "https://canary.com.br",
        "description": "Early and late stage VC backing exceptional teams across Brazil and Latin America. Focus on fintech, SaaS, healthcare, edtech, proptech. $15M avg check. 238 investments, 16 exits.",
        "investor_category": "seed_series_a",
    },
    {
        "name": "Bossa Invest",
        "slug": "bossa-invest",
        "type": "vc",
        "hq_location": "São Paulo, Brazil",
        "website": "https://bossainvest.com",
        "description": "One of the most prolific early-stage investors in Brazil with 2,200+ investments. Pre-seed and seed focus. R$1.5M avg check. 250 exits including 24 IPOs.",
        "investor_category": "pre_seed_fund",
    },
    {
        "name": "Monashees",
        "slug": "monashees",
        "type": "vc",
        "hq_location": "São Paulo, Brazil",
        "website": "https://monashees.com",
        "description": "One of Brazil's most respected VC firms. Deep operational support and global connectivity. 293 investments, 44 exits. Strong in fintech and SaaS.",
        "investor_category": "seed_series_a",
    },
    {
        "name": "Mindset Ventures",
        "slug": "mindset-ventures",
        "type": "vc",
        "hq_location": "São Paulo, Brazil",
        "website": "https://mindset.ventures",
        "description": "Early stage and seed investor. 95 investments, 11 exits. Focus on agriculture, financial services, healthcare.",
        "investor_category": "seed_series_a",
    },
    {
        "name": "DOMO.VC",
        "slug": "domo-vc",
        "type": "vc",
        "hq_location": "São Paulo, Brazil",
        "website": "https://domo.vc",
        "description": "Crowdfunding and early stage investor. 157 investments, 8 exits. Portfolio includes Loggi, Gympass, Hotmart.",
        "investor_category": "seed_series_a",
    },
    {
        "name": "ONEVC",
        "slug": "onevc",
        "type": "vc",
        "hq_location": "São Paulo, Brazil",
        "website": "https://onevc.vc",
        "description": "Early and late stage investor. 79 investments. Founded 2018.",
        "investor_category": "seed_series_a",
    },
    {
        "name": "Atlantico",
        "slug": "atlantico",
        "type": "vc",
        "hq_location": "São Paulo, Brazil",
        "website": "https://atlantico.vc",
        "description": "Early stage VC. 23 investments. Portfolio includes Kavak and Loft. Founded 2019.",
        "investor_category": "seed_series_a",
    },
    {
        "name": "Indicator Capital",
        "slug": "indicator-capital",
        "type": "vc",
        "hq_location": "São Paulo, Brazil",
        "website": "https://indicatorcapital.com",
        "description": "Early stage and seed investor specializing in IoT. 40 investments, 4 exits. Founded 2014.",
        "investor_category": "seed_series_a",
    },
    {
        "name": "Latitud",
        "slug": "latitud",
        "type": "vc",
        "hq_location": "São Paulo, Brazil",
        "website": "https://latitud.com",
        "description": "Early stage and seed investor. 127 investments, 5 exits. Founded by Brian Requarth (VivaReal founder). Founded 2020.",
        "investor_category": "seed_series_a",
    },
    {
        "name": "WOW Aceleradora",
        "slug": "wow-aceleradora",
        "type": "vc",
        "hq_location": "São Paulo, Brazil",
        "website": "https://wow.ac",
        "description": "Accelerator and seed investor backed by 300 angel investors. 185 investments, 10 exits. Founded 2013.",
        "investor_category": "pre_seed_fund",
    },
    {
        "name": "DGF Investimentos",
        "slug": "dgf-investimentos",
        "type": "vc",
        "hq_location": "São Paulo, Brazil",
        "website": "https://dgf.com.br",
        "description": "B2B software focused VC. Early to late stage. 53 investments, 13 exits. Portfolio includes RD Station. Founded 2001.",
        "investor_category": "seed_series_a",
    },
    {
        "name": "Redpoint eventures",
        "slug": "redpoint-eventures",
        "type": "vc",
        "hq_location": "São Paulo, Brazil",
        "website": "https://rpev.com.br",
        "description": "Early stage VC partnered with Redpoint Ventures (US). 116 investments, 17 exits. Founded 2011.",
        "investor_category": "seed_series_a",
    },
    {
        "name": "Igah Ventures",
        "slug": "igah-ventures",
        "type": "vc",
        "hq_location": "São Paulo, Brazil",
        "website": "https://igahventures.com",
        "description": "Growth stage investor. 70 investments, 9 exits. Founded 2010.",
        "investor_category": "growth",
    },
    {
        "name": "Norte Ventures",
        "slug": "norte-ventures",
        "type": "vc",
        "hq_location": "São Paulo, Brazil",
        "website": "https://norte.ventures",
        "description": "Prolific early stage investor. 145 investments, 6 exits. Founded 2020.",
        "investor_category": "seed_series_a",
    },
    {
        "name": "Caravela Capital",
        "slug": "caravela-capital",
        "type": "vc",
        "hq_location": "São Paulo, Brazil",
        "website": "https://caravela.capital",
        "description": "Early stage investor. 56 investments. Founded 2019.",
        "investor_category": "seed_series_a",
    },
    {
        "name": "ACE Ventures",
        "slug": "ace-ventures",
        "type": "vc",
        "hq_location": "São Paulo, Brazil",
        "website": "https://aceventures.com.br",
        "description": "Early stage investor and asset manager. 100+ investments with multiple exits across Brazil and Latin America.",
        "investor_category": "seed_series_a",
    },
    {
        "name": "MAYA Capital",
        "slug": "maya-capital",
        "type": "vc",
        "hq_location": "São Paulo, Brazil",
        "website": "https://maya.capital",
        "description": "Impact-driven early stage VC backing startups solving systemic problems across Latin America.",
        "investor_category": "seed_series_a",
    },
    {
        "name": "Iporanga Ventures",
        "slug": "iporanga-ventures",
        "type": "vc",
        "hq_location": "São Paulo, Brazil",
        "website": "https://iporangaventures.com",
        "description": "Agile early stage VC backing bold founders. Active in SaaS, edtech, and creator economy.",
        "investor_category": "seed_series_a",
    },
    {
        "name": "Alexia Ventures",
        "slug": "alexia-ventures",
        "type": "vc",
        "hq_location": "São Paulo, Brazil",
        "website": "https://alexia.vc",
        "description": "Seed and Series A investment fund supporting leading entrepreneurs from Brazil and Latin America.",
        "investor_category": "seed_series_a",
    },
    {
        "name": "ABSeed Ventures",
        "slug": "abseed-ventures",
        "type": "vc",
        "hq_location": "São Paulo, Brazil",
        "website": "https://abseed.com.br",
        "description": "Seed stage investor. 28 investments. Founded 2016.",
        "investor_category": "pre_seed_fund",
    },
    {
        "name": "Cedro Capital",
        "slug": "cedro-capital",
        "type": "vc",
        "hq_location": "São Paulo, Brazil",
        "website": "https://cedrocapital.com",
        "description": "Early stage investor. 30 investments, 6 exits. Founded 2013.",
        "investor_category": "seed_series_a",
    },
    {
        "name": "Terracotta Ventures",
        "slug": "terracotta-ventures",
        "type": "vc",
        "hq_location": "São Paulo, Brazil",
        "website": "https://terracotta.ventures",
        "description": "Early stage investor. 20 investments. Focus on proptech and contech. Founded 2019.",
        "investor_category": "seed_series_a",
    },
    # ── Mexico ──
    {
        "name": "Dalus Capital",
        "slug": "dalus-capital",
        "type": "vc",
        "hq_location": "Monterrey / Mexico City, Mexico",
        "website": "https://daluscapital.com",
        "description": "Series A and B investor. $4M avg check. Focus on fintech, edtech, healthtech, HRtech, climate, SaaS. Investing in scalable solutions for significant problems in LATAM.",
        "investor_category": "seed_series_a",
    },
    {
        "name": "Redwood Ventures",
        "slug": "redwood-ventures",
        "type": "vc",
        "hq_location": "Guadalajara, Mexico",
        "website": "https://redwood.ventures",
        "description": "Pre-seed to Series A. $200K checks. Industry-agnostic tech investor focusing on North American companies. Founded 2017.",
        "investor_category": "pre_seed_fund",
    },
    {
        "name": "Lotux",
        "slug": "lotux",
        "type": "vc",
        "hq_location": "Mexico City, Mexico",
        "website": "https://lotux.vc",
        "description": "Pre-seed and angel investor. $50K checks. Mission-driven software founders building for Latin America's underserved populations. Invests pre-revenue.",
        "investor_category": "pre_seed_fund",
    },
    {
        "name": "Poligono Capital",
        "slug": "poligono-capital",
        "type": "vc",
        "hq_location": "Guadalajara, Mexico",
        "website": "https://poligonocapital.com",
        "description": "Seed and Series A investor. $100K checks. Early-stage tech startups.",
        "investor_category": "seed_series_a",
    },
    {
        "name": "Hi Ventures",
        "slug": "hi-ventures",
        "type": "vc",
        "hq_location": "Mexico City, Mexico",
        "website": "https://hi.vc",
        "description": "Seed and early stage. 40+ portfolio companies across four funds. Focus on fintech, commerce, human capital, smart cities.",
        "investor_category": "seed_series_a",
    },
    {
        "name": "Jaguar Ventures",
        "slug": "jaguar-ventures",
        "type": "vc",
        "hq_location": "Mexico City, Mexico",
        "website": "https://jaguarvc.com",
        "description": "Seed and Series A. Requires $50K+ monthly revenue with 10%+ monthly growth. Technology sector focus.",
        "investor_category": "seed_series_a",
    },
    {
        "name": "COMETA",
        "slug": "cometa",
        "type": "vc",
        "hq_location": "Mexico City, Mexico",
        "website": "https://cometa.vc",
        "description": "Pre-seed to early stage. Tech targeting Spanish-speaking markets since 2013. Focus on e-commerce enablers, fintech, marketplaces.",
        "investor_category": "pre_seed_fund",
    },
    {
        "name": "DILA Capital",
        "slug": "dila-capital",
        "type": "vc",
        "hq_location": "Mexico City, Mexico",
        "website": "https://dilacapital.com",
        "description": "Seed and early stage. Specializing in Latin America and Hispanic US market. Provides funding, mentorship, and strategic insight.",
        "investor_category": "seed_series_a",
    },
    {
        "name": "Avalancha Ventures",
        "slug": "avalancha-ventures",
        "type": "vc",
        "hq_location": "Mexico City, Mexico",
        "website": "https://avalanchaventures.com",
        "description": "Early stage. Up to $300K first ticket, up to $2M follow-on. Tech for underserved populations. Founded 2015.",
        "investor_category": "pre_seed_fund",
    },
    {
        "name": "500 LatAm",
        "slug": "500-latam",
        "type": "vc",
        "hq_location": "Mexico City, Mexico",
        "website": "https://500.co",
        "description": "Part of 500 Global. Early stage investing in Spanish-speaking Latin American startups. $300K typical investment with strategic support and Silicon Valley access.",
        "investor_category": "pre_seed_fund",
    },
    {
        "name": "Bridge Latam",
        "slug": "bridge-latam",
        "type": "vc",
        "hq_location": "Mexico City, Mexico",
        "website": "https://bridgelat.com",
        "description": "Seed stage investor focused on Latam-based companies.",
        "investor_category": "pre_seed_fund",
    },
    {
        "name": "COLABORATIVOx",
        "slug": "colaborativox",
        "type": "vc",
        "hq_location": "Mexico City, Mexico",
        "website": "https://colaborativox.com",
        "description": "Accelerator, seed, early stage. Impact tech solving complex problems for sustainable development in emerging markets.",
        "investor_category": "pre_seed_fund",
    },
    # ── Colombia ──
    {
        "name": "Veronorte",
        "slug": "veronorte",
        "type": "vc",
        "hq_location": "Medellín, Colombia",
        "website": "https://veronorte.com",
        "description": "24 portfolio companies, 10 exits. Focus on sustainable ventures across Latin America. Based in Medellín.",
        "investor_category": "seed_series_a",
    },
    {
        "name": "Polymath Ventures",
        "slug": "polymath-ventures",
        "type": "vc",
        "hq_location": "Bogotá, Colombia",
        "website": "https://polymathv.com",
        "description": "Venture studio. Founded 2012. Launched 9 companies, raised $30M+. Builds human-centered digital firms for emerging markets.",
        "investor_category": "seed_series_a",
    },
    {
        "name": "Simma Capital",
        "slug": "simma-capital",
        "type": "vc",
        "hq_location": "Bogotá, Colombia",
        "website": "https://simmacapital.com",
        "description": "Early and growth stage. Strong connections to Colombia and Latin America. Backs outstanding teams with scalable business models.",
        "investor_category": "seed_series_a",
    },
    {
        "name": "Latin Leap",
        "slug": "latin-leap",
        "type": "vc",
        "hq_location": "Bogotá, Colombia",
        "website": "https://latinleap.com",
        "description": "Venture Capital Studio. Actively builds and funds technology companies. Seed through growth stages.",
        "investor_category": "seed_series_a",
    },
    # ── Regional (multi-country Latam) ──
    {
        "name": "Kaszek Ventures",
        "slug": "kaszek-ventures",
        "type": "vc",
        "hq_location": "Buenos Aires / São Paulo / Mexico City",
        "website": "https://kaszek.com",
        "description": "Latin America's largest VC firm. Seed to Series B. Half of investments in Brazil, followed by Mexico, Argentina, Colombia, Chile. Accelerated investment pace in 2025.",
        "investor_category": "seed_series_a",
    },
    {
        "name": "NXTP Ventures",
        "slug": "nxtp-ventures",
        "type": "vc",
        "hq_location": "Buenos Aires / Mexico City",
        "website": "https://nxtpventures.com",
        "description": "Latam seed fund. B2B companies from idea to execution. Cloud, SaaS, eCommerce, fintech, AI. Seed check $500K-$3M, Series A $2M-$5M.",
        "investor_category": "seed_series_a",
    },
    {
        "name": "Magma Partners",
        "slug": "magma-partners",
        "type": "vc",
        "hq_location": "Santiago, Chile",
        "website": "https://magmapartners.com",
        "description": "Pre-seed to Series A. Top Latin American founders solving the region's biggest problems. Fintech, insurtech, marketplaces.",
        "investor_category": "pre_seed_fund",
    },
    {
        "name": "Carao Ventures",
        "slug": "carao-ventures",
        "type": "vc",
        "hq_location": "San José, Costa Rica",
        "website": "https://caraoventures.com",
        "description": "Early stage VC across Spanish-speaking Latin America. Pre-seed to pre-Series A. Central America, Colombia, Ecuador, Peru.",
        "investor_category": "pre_seed_fund",
    },
    {
        "name": "Alaya Capital",
        "slug": "alaya-capital",
        "type": "vc",
        "hq_location": "Santiago, Chile",
        "website": "https://alayacapital.com",
        "description": "Seed and Series A. $500K-$1M checks. High-tech startups with exceptional founders across Spanish-speaking Latin America.",
        "investor_category": "seed_series_a",
    },
    {
        "name": "Newtopia VC",
        "slug": "newtopia-vc",
        "type": "vc",
        "hq_location": "Regional (Latam)",
        "website": "https://newtopia.vc",
        "description": "Pre-seed and seed investments in early stage Latam startups.",
        "investor_category": "pre_seed_fund",
    },
    {
        "name": "ALLVP",
        "slug": "allvp",
        "type": "vc",
        "hq_location": "Mexico City, Mexico",
        "website": "https://allvp.com",
        "description": "Early stage VC. Exited Cornershop to Walmart for $225M. One of the pioneering Latam VC firms.",
        "investor_category": "seed_series_a",
    },
    # ── Additional from Shizune data ──
    {
        "name": "Nazca Ventures",
        "slug": "nazca-ventures",
        "type": "vc",
        "hq_location": "Santiago, Chile",
        "website": "https://nazca.vc",
        "description": "Seed and Series A. 33 investments. Focus on e-commerce, fintech, software across Latam.",
        "investor_category": "seed_series_a",
    },
    {
        "name": "The Ark Fund",
        "slug": "the-ark-fund",
        "type": "vc",
        "hq_location": "Mexico City, Mexico",
        "website": "https://arkfund.co",
        "description": "Seed and pre-seed. 29 investments. Fintech and software focus.",
        "investor_category": "pre_seed_fund",
    },
    {
        "name": "Angel Ventures",
        "slug": "angel-ventures",
        "type": "vc",
        "hq_location": "Mexico City, Mexico",
        "website": "https://angelventures.vc",
        "description": "Seed and Series A. 22 investments. E-commerce, software focus.",
        "investor_category": "seed_series_a",
    },
    {
        "name": "IGNIA",
        "slug": "ignia",
        "type": "vc",
        "hq_location": "San Pedro, Mexico",
        "website": "https://ignia.vc",
        "description": "Seed to Series B. 20 investments. Fintech, internet focus. Impact-driven.",
        "investor_category": "seed_series_a",
    },
    {
        "name": "Investo",
        "slug": "investo",
        "type": "vc",
        "hq_location": "Mexico City, Mexico",
        "website": "https://investovc.com",
        "description": "Seed, pre-seed, angel. 15 investments. E-commerce, fintech focus.",
        "investor_category": "pre_seed_fund",
    },
    {
        "name": "Capital Invent",
        "slug": "capital-invent",
        "type": "vc",
        "hq_location": "Mexico City, Mexico",
        "website": "https://capitalinvent.com",
        "description": "Seed and Series A. 12 investments. E-commerce, software, fintech.",
        "investor_category": "seed_series_a",
    },
    {
        "name": "G2 Momentum Capital",
        "slug": "g2-momentum-capital",
        "type": "vc",
        "hq_location": "Mexico City, Mexico",
        "website": "https://g2momentum.capital",
        "description": "Seed and pre-seed. 12 investments. Fintech, mobile apps.",
        "investor_category": "pre_seed_fund",
    },
    {
        "name": "CARABELA",
        "slug": "carabela",
        "type": "vc",
        "hq_location": "Guadalajara, Mexico",
        "website": "https://carabela.vc",
        "description": "Seed and pre-seed. 10 investments. SaaS focus.",
        "investor_category": "pre_seed_fund",
    },
    {
        "name": "Dux Capital",
        "slug": "dux-capital",
        "type": "vc",
        "hq_location": "Mexico City, Mexico",
        "website": "https://duxcapital.vc",
        "description": "Seed and pre-seed. 7 investments. E-commerce, AI, fintech.",
        "investor_category": "pre_seed_fund",
    },
    {
        "name": "Proeza Ventures",
        "slug": "proeza-ventures",
        "type": "vc",
        "hq_location": "Monterrey, Mexico",
        "website": "https://proezaventures.com",
        "description": "Seed and Series A. 5 investments. Transportation, automotive, software.",
        "investor_category": "seed_series_a",
    },
    {
        "name": "ArkAngeles",
        "slug": "arkangeles",
        "type": "vc",
        "hq_location": "Mexico City, Mexico",
        "website": "https://arkangeles.co",
        "description": "Seed and angel. 5 investments. Software, fintech, ML.",
        "investor_category": "pre_seed_fund",
    },
    {
        "name": "Marathon Ventures",
        "slug": "marathon-ventures",
        "type": "vc",
        "hq_location": "Bogotá, Colombia",
        "website": "https://marathonvc.com",
        "description": "Pre-seed and seed. 6 investments. E-commerce, software, SaaS. Colombia-focused.",
        "investor_category": "pre_seed_fund",
    },
    {
        "name": "Endeavor Catalyst",
        "slug": "endeavor-catalyst",
        "type": "vc",
        "hq_location": "New York, USA",
        "website": "https://endeavor.org/catalyst",
        "description": "Series A-C co-investment fund. Backs Endeavor entrepreneurs globally with strong Latam presence.",
        "investor_category": "growth",
    },
    {
        "name": "FJ Labs",
        "slug": "fj-labs",
        "type": "vc",
        "hq_location": "New York, USA",
        "website": "https://fjlabs.com",
        "description": "Seed to Series B. 11 Mexico investments. Software, e-commerce, fintech. Founded by Fabrice Grinda.",
        "investor_category": "seed_series_a",
    },
    {
        "name": "LEAP Global Partners",
        "slug": "leap-global-partners",
        "type": "vc",
        "hq_location": "Palo Alto, USA",
        "website": "https://leapglobalpartners.com",
        "description": "Seed and Series A. 7 investments in Mexico. Fintech, software focus. Bridge between Silicon Valley and Latam.",
        "investor_category": "seed_series_a",
    },
    {
        "name": "Quona Capital",
        "slug": "quona-capital",
        "type": "vc",
        "hq_location": "Washington D.C., USA",
        "website": "https://quona.com",
        "description": "Series A and seed. 7 Mexico investments. Financial inclusion focused. Fintech across emerging markets.",
        "investor_category": "seed_series_a",
    },
]


class LatamVCDirectory(BaseEnricher):
    """Seed the database with curated Latam VC investor profiles."""

    def source_name(self) -> str:
        return "latam_vc_directory"

    async def enrich(self, session: AsyncSession) -> EnrichmentResult:
        result = EnrichmentResult(source=self.source_name())

        for vc in LATAM_VCS:
            try:
                # Check if investor exists
                existing = await session.execute(
                    select(Investor).where(Investor.slug == vc["slug"])
                )
                investor = existing.scalar_one_or_none()

                if investor:
                    # Update missing fields only
                    updated = False
                    if not investor.description and vc.get("description"):
                        investor.description = vc["description"]
                        updated = True
                    if not investor.hq_location and vc.get("hq_location"):
                        investor.hq_location = vc["hq_location"]
                        updated = True
                    if not investor.website and vc.get("website"):
                        investor.website = vc["website"]
                        updated = True
                    if not investor.type and vc.get("type"):
                        investor.type = vc["type"]
                        updated = True
                    if not investor.investor_category and vc.get("investor_category"):
                        investor.investor_category = vc["investor_category"]
                        updated = True

                    if updated:
                        result.records_updated += 1
                    else:
                        result.records_skipped += 1
                else:
                    # Create new investor
                    investor = Investor(
                        name=vc["name"],
                        slug=vc["slug"],
                        type=vc.get("type", "vc"),
                        website=vc.get("website"),
                        description=vc.get("description"),
                        hq_location=vc.get("hq_location"),
                        investor_category=vc.get("investor_category"),
                    )
                    session.add(investor)
                    result.records_updated += 1
                    logger.info("Created Latam VC: %s (%s)", vc["name"], vc.get("hq_location"))

            except Exception as e:
                result.errors.append(f"{vc['name']}: {e}")
                logger.exception("Error processing Latam VC: %s", vc["name"])

        await session.flush()
        logger.info(
            "Latam VC Directory: %d updated, %d skipped, %d errors",
            result.records_updated,
            result.records_skipped,
            len(result.errors),
        )
        return result
