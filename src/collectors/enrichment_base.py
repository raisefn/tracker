"""Base class for enrichment collectors that update existing records."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class EnrichmentResult:
    """Result of an enrichment run."""

    source: str
    records_updated: int = 0
    records_skipped: int = 0
    errors: list[str] = field(default_factory=list)


def stamp_freshness(project: "Project", source: str) -> None:  # noqa: F821
    """Update per-source freshness timestamp on a project."""
    current = project.source_freshness or {}
    current[source] = datetime.now(timezone.utc).isoformat()
    project.source_freshness = current


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
