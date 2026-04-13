"""Webhook management endpoints."""

import ipaddress
import secrets
import uuid
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, HttpUrl
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_db
from src.models.webhook import Webhook

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

VALID_EVENTS = ["round.created", "round.updated"]

# Blocked IP ranges (SSRF protection)
_BLOCKED_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),  # link-local / cloud metadata
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
]


def _validate_webhook_url(url: str) -> None:
    """Block internal/private IPs to prevent SSRF."""
    parsed = urlparse(url)
    if parsed.scheme not in ("https",):
        raise HTTPException(400, "Webhook URL must use HTTPS")
    hostname = parsed.hostname
    if not hostname:
        raise HTTPException(400, "Invalid webhook URL")
    try:
        addr = ipaddress.ip_address(hostname)
        if any(addr in net for net in _BLOCKED_NETWORKS):
            raise HTTPException(400, "Webhook URL must not point to a private/internal address")
    except ValueError:
        # hostname is a domain name, not an IP — allowed
        # (DNS rebinding is a separate concern, but this blocks the obvious cases)
        pass


class WebhookCreate(BaseModel):
    url: HttpUrl
    events: list[str]
    owner: str


class WebhookOut(BaseModel):
    id: uuid.UUID
    url: str
    events: list[str]
    owner: str
    is_active: bool

    model_config = {"from_attributes": True}


class WebhookCreated(WebhookOut):
    secret: str


@router.post("", response_model=WebhookCreated, status_code=201)
async def create_webhook(
    body: WebhookCreate,
    db: AsyncSession = Depends(get_db),
):
    _validate_webhook_url(str(body.url))

    for event in body.events:
        if event not in VALID_EVENTS:
            raise HTTPException(400, f"Invalid event: {event}. Valid: {VALID_EVENTS}")

    secret = secrets.token_hex(32)
    wh = Webhook(
        url=str(body.url),
        events=body.events,
        secret=secret,
        owner=body.owner,
    )
    db.add(wh)
    await db.commit()
    await db.refresh(wh)
    return WebhookCreated(
        id=wh.id,
        url=wh.url,
        events=wh.events,
        owner=wh.owner,
        is_active=wh.is_active,
        secret=secret,
    )


@router.get("", response_model=list[WebhookOut])
async def list_webhooks(
    owner: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Webhook).where(Webhook.owner == owner))
    return result.scalars().all()


@router.delete("/{webhook_id}", status_code=204)
async def delete_webhook(
    webhook_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Webhook).where(Webhook.id == webhook_id))
    wh = result.scalar_one_or_none()
    if not wh:
        raise HTTPException(404, "Webhook not found")
    await db.delete(wh)
    await db.commit()
