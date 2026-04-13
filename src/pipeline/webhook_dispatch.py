"""Dispatch webhook events to registered endpoints."""

import hashlib
import hmac
import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.webhook import Webhook

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
TIMEOUT_SECONDS = 10


def _sign_payload(payload: bytes, secret: str) -> str:
    """Create HMAC-SHA256 signature for webhook payload."""
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


async def dispatch_event(session: AsyncSession, event: str, data: dict) -> int:
    """Send event to all active webhooks subscribed to this event type.

    Returns the number of successful deliveries.
    """
    result = await session.execute(select(Webhook).where(Webhook.is_active.is_(True)))
    webhooks = result.scalars().all()

    delivered = 0
    for wh in webhooks:
        if event not in wh.events:
            continue

        payload_dict = {
            "event": event,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": data,
        }

        import json

        payload_bytes = json.dumps(payload_dict, default=str).encode()
        signature = _sign_payload(payload_bytes, wh.secret)

        headers = {
            "Content-Type": "application/json",
            "X-Webhook-Signature": signature,
            "X-Webhook-Event": event,
        }

        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            for attempt in range(MAX_RETRIES):
                try:
                    resp = await client.post(wh.url, content=payload_bytes, headers=headers)
                    if resp.status_code < 400:
                        delivered += 1
                        break
                    logger.warning(
                        "Webhook %s returned %d (attempt %d/%d)",
                        wh.url,
                        resp.status_code,
                        attempt + 1,
                        MAX_RETRIES,
                    )
                except Exception as e:
                    logger.warning(
                        "Webhook %s failed (attempt %d/%d): %s",
                        wh.url,
                        attempt + 1,
                        MAX_RETRIES,
                        e,
                    )

    return delivered
