"""Investor intel submission endpoint."""

import hashlib
import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Security
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_db
from src.models.contributor import Contributor
from src.models.investor_intel import InvestorIntel
from src.pipeline.normalizer import make_slug

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/intel", tags=["intel"])

intel_token_header = APIKeyHeader(name="X-Intel-Token", auto_error=False)


async def require_contributor(
    token: str | None = Security(intel_token_header),
    db: AsyncSession = Depends(get_db),
) -> Contributor:
    if not token:
        raise HTTPException(status_code=401, detail="Intel token required (X-Intel-Token header)")

    token_hash = hashlib.sha256(token.encode()).hexdigest()
    result = await db.execute(
        select(Contributor).where(
            Contributor.api_token_hash == token_hash,
            Contributor.disabled_at.is_(None),
        )
    )
    contributor = result.scalar_one_or_none()
    if contributor is None:
        raise HTTPException(status_code=401, detail="Invalid or disabled contributor token")
    return contributor


# --- Request/Response schemas ---


class IntelSubmission(BaseModel):
    investor_name: str = Field(..., min_length=2, description="Investor name")
    intel_type: str = Field(
        default="meeting",
        description="Type: meeting, hearsay, public_signal, portfolio_move",
    )
    raw_text: str = Field(..., min_length=10, description="Freeform intel text")
    confidence: str = Field(default="firsthand", description="firsthand, secondhand, or rumor")
    observed_at: date | None = Field(default=None, description="When the intel was observed")

    # Optional structured fields — can also be extracted from raw_text later
    deployment_focus: str | None = None
    check_size_min: int | None = None
    check_size_max: int | None = None
    fund_stage: str | None = None
    key_partners: list[str] | None = None
    pass_patterns: str | None = None
    excitement_signals: str | None = None
    portfolio_intel: str | None = None
    meeting_context: str | None = None
    location: str | None = None


class IntelResponse(BaseModel):
    id: str
    investor_slug: str
    status: str
    message: str


# --- Endpoint ---


@router.post("", response_model=IntelResponse)
async def submit_intel(
    body: IntelSubmission,
    contributor: Contributor = Depends(require_contributor),
    db: AsyncSession = Depends(get_db),
):
    """Submit investor intel. Authenticated via X-Intel-Token header."""
    # Validate intel_type
    valid_types = {"meeting", "hearsay", "public_signal", "portfolio_move"}
    if body.intel_type not in valid_types:
        raise HTTPException(status_code=422, detail=f"intel_type must be one of: {', '.join(valid_types)}")

    valid_confidence = {"firsthand", "secondhand", "rumor"}
    if body.confidence not in valid_confidence:
        raise HTTPException(status_code=422, detail=f"confidence must be one of: {', '.join(valid_confidence)}")

    investor_slug = make_slug(body.investor_name)

    # Auto-approve for admin/trusted, queue for contributors
    status = "approved" if contributor.trust_tier in ("admin", "trusted") else "pending"

    record = InvestorIntel(
        contributor_id=contributor.id,
        investor_slug=investor_slug,
        investor_name=body.investor_name,
        intel_type=body.intel_type,
        raw_text=body.raw_text,
        confidence=body.confidence,
        observed_at=body.observed_at,
        status=status,
        deployment_focus=body.deployment_focus,
        check_size_min=body.check_size_min,
        check_size_max=body.check_size_max,
        fund_stage=body.fund_stage,
        key_partners=body.key_partners,
        pass_patterns=body.pass_patterns,
        excitement_signals=body.excitement_signals,
        portfolio_intel=body.portfolio_intel,
        meeting_context=body.meeting_context,
        location=body.location,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)

    logger.info(
        "Intel submitted by %s for %s (type=%s, status=%s)",
        contributor.name, investor_slug, body.intel_type, status,
    )

    return IntelResponse(
        id=str(record.id),
        investor_slug=investor_slug,
        status=status,
        message="Intel recorded" if status == "approved" else "Intel queued for review",
    )
