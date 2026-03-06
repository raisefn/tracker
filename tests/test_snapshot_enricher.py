"""Tests for Snapshot governance enricher."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.collectors.snapshot_enricher import SnapshotEnricher
from src.models import Project


@pytest.mark.asyncio
async def test_enriches_project_with_snapshot_data(db_session):
    proj = Project(
        name="Uniswap", slug="uniswap", snapshot_space="uniswap.eth"
    )
    db_session.add(proj)
    await db_session.flush()

    async def mock_post(url, **kwargs):
        body = kwargs.get("json", {})
        query = body.get("query", "")
        resp = MagicMock()
        resp.status_code = 200

        if "space(id:" in query:
            resp.json.return_value = {
                "data": {"space": {"id": "uniswap.eth", "name": "Uniswap", "proposalsCount": 50}}
            }
        else:
            resp.json.return_value = {
                "data": {
                    "proposals": [
                        {"id": "1", "created": 1700000000, "votes": 100},
                        {"id": "2", "created": 1700000001, "votes": 200},
                    ],
                    "recent": [{"id": "1"}],
                }
            }
        return resp

    with patch("src.collectors.snapshot_enricher.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.post = mock_post
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = mock_client

        enricher = SnapshotEnricher()
        result = await enricher.enrich(db_session)

    assert result.records_updated == 1
    assert proj.snapshot_proposals_count == 2
    assert proj.snapshot_voters_count == 300
    assert proj.snapshot_proposal_activity_30d == 1


@pytest.mark.asyncio
async def test_skips_project_without_snapshot_space(db_session):
    proj = Project(name="NoSnap", slug="no-snap")
    db_session.add(proj)
    await db_session.flush()

    # Mock that no space is found
    async def mock_post(url, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"data": {"space": None}}
        return resp

    with patch("src.collectors.snapshot_enricher.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.post = mock_post
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = mock_client

        enricher = SnapshotEnricher()
        result = await enricher.enrich(db_session)

    assert result.records_skipped >= 1
    assert proj.snapshot_proposals_count is None
