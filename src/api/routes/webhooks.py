"""Webhook management endpoints."""

import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, HttpUrl
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_db
from src.models.webhook import Webhook

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

VALID_EVENTS = ["round.created", "round.updated"]


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
        id=wh.id, url=wh.url, events=wh.events,
        owner=wh.owner, is_active=wh.is_active, secret=secret,
    )


@router.get("", response_model=list[WebhookOut])
async def list_webhooks(
    owner: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Webhook).where(Webhook.owner == owner)
    )
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
