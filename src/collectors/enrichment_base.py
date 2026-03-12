"""Base class for enrichment collectors that update existing records."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified


@dataclass
class EnrichmentResult:
    """Result of an enrichment run."""

    source: str
    records_updated: int = 0
    records_skipped: int = 0
    errors: list[str] = field(default_factory=list)


def stamp_freshness(project, source: str) -> None:
    """Update per-source freshness timestamp on a project or investor."""
    current = project.source_freshness or {}
    current[source] = datetime.now(timezone.utc).isoformat()
    project.source_freshness = current
    flag_modified(project, "source_freshness")


# Common firm name suffixes to strip for fuzzy slug matching
FIRM_SUFFIXES = [
    "llc", "lp", "inc", "corp", "ltd", "co", "group",
    "management", "advisory", "advisors", "advisers",
    "partners", "capital", "ventures", "fund", "holdings",
    "investments", "investment", "trust", "foundation",
    "associates", "global", "international",
]


def normalize_firm_slug(name: str) -> str:
    """Normalize a firm name for fuzzy matching by stripping common suffixes."""
    from src.pipeline.normalizer import make_slug

    lower = name.lower()
    for suffix in FIRM_SUFFIXES:
        lower = lower.replace(f" {suffix}.", "").replace(f" {suffix}", "")
    return make_slug(lower.strip())


async def find_investor_match(
    session: AsyncSession, name: str, **identifiers
) -> "Investor | None":  # noqa: F821
    """Try to match an investor by identifiers, exact slug, normalized slug, or prefix."""
    from src.models import Investor
    from src.pipeline.normalizer import make_slug

    # 1. Identifier match (CRD, CIK, EIN)
    for field_name, value in identifiers.items():
        if value:
            result = await session.execute(
                select(Investor).where(getattr(Investor, field_name) == value)
            )
            investor = result.scalar_one_or_none()
            if investor:
                return investor

    # 2. Exact slug match
    slug = make_slug(name)
    result = await session.execute(select(Investor).where(Investor.slug == slug))
    investor = result.scalar_one_or_none()
    if investor:
        return investor

    # 3. Normalized slug match
    norm_slug = normalize_firm_slug(name)
    if norm_slug and norm_slug != slug:
        result = await session.execute(select(Investor).where(Investor.slug == norm_slug))
        investor = result.scalar_one_or_none()
        if investor:
            return investor

    # 4. Prefix match with length guard
    if norm_slug and len(norm_slug) >= 4:
        result = await session.execute(
            select(Investor).where(
                Investor.slug.like(f"{norm_slug}%"),
                func.length(Investor.slug) <= int(len(norm_slug) * 1.3),
                func.length(Investor.slug) >= int(len(norm_slug) * 0.7),
            ).limit(1)
        )
        investor = result.scalar_one_or_none()
        if investor:
            return investor

    return None


class BaseEnricher(ABC):
    """Base class for enrichment collectors."""

    @abstractmethod
    async def enrich(self, session: AsyncSession) -> EnrichmentResult:
        """Update existing records with external data."""
        ...

    @abstractmethod
    def source_name(self) -> str:
        """Return enricher identifier, e.g. 'defillama_protocols'."""
        ...
