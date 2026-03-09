from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date


@dataclass
class RawFounder:
    """Raw founder/executive data from a collector."""

    name: str
    role: str | None = None  # CEO, CTO, Co-Founder, Director, etc.
    linkedin: str | None = None
    twitter: str | None = None
    github: str | None = None


@dataclass
class RawRound:
    """Raw round data from a collector, before normalization."""

    project_name: str
    date: date
    source_url: str | None = None
    round_type: str | None = None
    amount_usd: int | None = None
    valuation_usd: int | None = None
    lead_investors: list[str] = field(default_factory=list)
    other_investors: list[str] = field(default_factory=list)
    founders: list[RawFounder] = field(default_factory=list)
    sector: str | None = None
    category: str | None = None
    chains: list[str] = field(default_factory=list)
    project_url: str | None = None
    raw_data: dict | None = None


class BaseCollector(ABC):
    """Base class for all data collectors."""

    @abstractmethod
    async def collect(self) -> list[RawRound]:
        """Fetch rounds from source."""
        ...

    @abstractmethod
    def source_type(self) -> str:
        """Return source identifier, e.g. 'defillama'."""
        ...
