"""CLI to create and manage API keys."""

import asyncio
import secrets
import sys

from sqlalchemy import select, update

from src.api.auth import hash_key
from src.db.session import async_session
from src.models.api_key import ApiKey


def generate_key() -> str:
    """Generate a 32-byte hex API key with rfn_ prefix."""
    return f"rfn_{secrets.token_hex(32)}"


async def create_key(owner: str, tier: str = "free") -> None:
    raw_key = generate_key()
    key_hash = hash_key(raw_key)

    async with async_session() as session:
        api_key = ApiKey(
            key_hash=key_hash,
            key_prefix=raw_key[:8],
            owner=owner,
            tier=tier,
        )
        session.add(api_key)
        await session.commit()

    print(f"API key created for '{owner}' (tier: {tier})")
    print(f"Key: {raw_key}")
    print(f"Prefix: {raw_key[:8]}")
    print("Store this key securely — it will not be shown again.")


async def revoke_key(prefix: str) -> None:
    async with async_session() as session:
        result = await session.execute(
            update(ApiKey)
            .where(ApiKey.key_prefix == prefix, ApiKey.is_active.is_(True))
            .values(is_active=False)
            .returning(ApiKey.owner)
        )
        owner = result.scalar_one_or_none()
        if owner:
            await session.commit()
            print(f"Revoked key {prefix} (owner: {owner})")
        else:
            print(f"No active key found with prefix {prefix}")


async def list_keys() -> None:
    async with async_session() as session:
        result = await session.execute(select(ApiKey).order_by(ApiKey.created_at.desc()))
        keys = result.scalars().all()
        if not keys:
            print("No API keys found.")
            return
        for k in keys:
            status = "active" if k.is_active else "REVOKED"
            print(f"  {k.key_prefix}... | {k.owner:20s} | {k.tier:8s} | {status}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m scripts.manage_keys <create|revoke|list> [args]")
        print("  create <owner> [tier]  — Generate a new API key")
        print("  revoke <prefix>        — Revoke a key by its prefix")
        print("  list                   — List all keys")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "create":
        owner = sys.argv[2] if len(sys.argv) > 2 else "default"
        tier = sys.argv[3] if len(sys.argv) > 3 else "free"
        asyncio.run(create_key(owner, tier))
    elif cmd == "revoke":
        if len(sys.argv) < 3:
            print("Usage: python -m scripts.manage_keys revoke <prefix>")
            sys.exit(1)
        asyncio.run(revoke_key(sys.argv[2]))
    elif cmd == "list":
        asyncio.run(list_keys())
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
