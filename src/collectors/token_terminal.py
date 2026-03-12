"""Token Terminal enricher — on-chain revenue and financial metrics.

Token Terminal provides protocol-level financial data: revenue, fees,
earnings, P/S ratios. This is the "real metrics" layer for crypto
projects that DeFiLlama TVL alone doesn't capture.

Source: Token Terminal public API (limited free tier, no auth for basics).
"""

import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.collectors.enrichment_base import BaseEnricher, EnrichmentResult, stamp_freshness
from src.models import Project

logger = logging.getLogger(__name__)

TOKEN_TERMINAL_API = "https://api.tokenterminal.com/v2"


class TokenTerminalEnricher(BaseEnricher):
    """Enrich crypto projects with on-chain revenue and financial metrics."""

    def source_name(self) -> str:
        return "token_terminal"

    async def enrich(self, session: AsyncSession) -> EnrichmentResult:
        result = EnrichmentResult(source="token_terminal")

        # Fetch projects that are blockchain-related and have a slug
        query = select(Project).where(
            Project.sector == "blockchain",
            Project.slug.isnot(None),
        )
        rows = await session.execute(query)
        projects = list(rows.scalars().all())
        logger.info(f"Token Terminal: {len(projects)} blockchain projects to check")

        # First, fetch the full project list from Token Terminal
        project_map = await self._fetch_tt_projects()
        if not project_map:
            logger.warning("Token Terminal: could not fetch project list")
            return result

        async with httpx.AsyncClient(
            timeout=30,
            headers={"User-Agent": "raisefn/tracker (contact@raisefn.com)"},
        ) as client:
            for project in projects:
                # Try to match by slug
                tt_id = project_map.get(project.slug)
                if not tt_id and project.name:
                    # Try lowercase name match
                    tt_id = project_map.get(project.name.lower().replace(" ", "-"))
                if not tt_id:
                    result.records_skipped += 1
                    continue

                try:
                    metrics = await self._fetch_metrics(client, tt_id)
                    if metrics:
                        self._apply_metrics(project, metrics)
                        stamp_freshness(project, "token_terminal")
                        result.records_updated += 1
                    else:
                        result.records_skipped += 1
                except Exception as e:
                    result.errors.append(f"{project.slug}: {e}")

        await session.commit()
        return result

    async def _fetch_tt_projects(self) -> dict[str, str]:
        """Fetch Token Terminal project list and build slug→id map."""
        async with httpx.AsyncClient(
            timeout=30,
            headers={"User-Agent": "raisefn/tracker (contact@raisefn.com)"},
        ) as client:
            try:
                resp = await client.get(f"{TOKEN_TERMINAL_API}/projects")
                if resp.status_code in (401, 403):
                    logger.warning(f"Token Terminal API returned {resp.status_code}")
                    return {}
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                logger.error(f"Token Terminal project list fetch failed: {e}")
                return {}

        projects = data if isinstance(data, list) else data.get("data", [])
        slug_map: dict[str, str] = {}
        for p in projects:
            pid = p.get("project_id") or p.get("id") or p.get("slug")
            slug = p.get("slug") or p.get("project_id") or ""
            if pid and slug:
                slug_map[slug.lower()] = pid
                # Also map by name
                name = p.get("name") or ""
                if name:
                    slug_map[name.lower().replace(" ", "-")] = pid

        return slug_map

    async def _fetch_metrics(self, client: httpx.AsyncClient, project_id: str) -> dict | None:
        """Fetch financial metrics for a single project."""
        try:
            resp = await client.get(
                f"{TOKEN_TERMINAL_API}/projects/{project_id}",
                params={"interval": "daily"},
            )
            if resp.status_code in (404, 403, 429):
                return None
            resp.raise_for_status()
            data = resp.json()
            return data.get("data") or data
        except Exception:
            return None

    @staticmethod
    def _apply_metrics(project: Project, metrics: dict) -> None:
        """Apply Token Terminal metrics to a project record."""
        raw = project.raw_data or {}

        # Revenue metrics
        revenue = metrics.get("revenue_30d") or metrics.get("revenue")
        if revenue:
            raw["tt_revenue_30d"] = revenue

        fees = metrics.get("fees_30d") or metrics.get("fees")
        if fees:
            raw["tt_fees_30d"] = fees

        earnings = metrics.get("earnings_30d") or metrics.get("earnings")
        if earnings:
            raw["tt_earnings_30d"] = earnings

        # Valuation ratios
        ps_ratio = metrics.get("ps_ratio") or metrics.get("price_to_sales")
        if ps_ratio:
            raw["tt_ps_ratio"] = ps_ratio

        pe_ratio = metrics.get("pe_ratio") or metrics.get("price_to_earnings")
        if pe_ratio:
            raw["tt_pe_ratio"] = pe_ratio

        # Active users / usage
        dau = metrics.get("daily_active_users") or metrics.get("dau")
        if dau:
            raw["tt_dau"] = dau

        raw["tt_updated"] = datetime.now(timezone.utc).isoformat()
        project.raw_data = raw
