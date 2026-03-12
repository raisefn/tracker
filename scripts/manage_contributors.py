"""CLI to manage intel contributors (invite, revoke, list)."""

import asyncio
import hashlib
import secrets
import sys

from sqlalchemy import select, update

from src.db.session import async_session
from src.models.contributor import Contributor


def _generate_token() -> str:
    """Generate a contributor API token with rfn_intel_ prefix."""
    return f"rfn_intel_{secrets.token_hex(24)}"


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


async def invite(name: str, email: str, tier: str = "contributor", invited_by: str = "justin") -> None:
    raw_token = _generate_token()
    token_hash = _hash_token(raw_token)

    async with async_session() as session:
        contributor = Contributor(
            name=name,
            email=email,
            trust_tier=tier,
            invited_by=invited_by,
            api_token_hash=token_hash,
            token_prefix=raw_token[:14],
        )
        session.add(contributor)
        await session.commit()

    print(f"Contributor invited: {name} ({email})")
    print(f"Trust tier: {tier}")
    print(f"Invited by: {invited_by}")
    print(f"Token: {raw_token}")
    print("Share this token securely — it will not be shown again.")


async def revoke(email: str) -> None:
    async with async_session() as session:
        from datetime import datetime, timezone

        result = await session.execute(
            update(Contributor)
            .where(Contributor.email == email, Contributor.disabled_at.is_(None))
            .values(disabled_at=datetime.now(timezone.utc))
            .returning(Contributor.name)
        )
        name = result.scalar_one_or_none()
        if name:
            await session.commit()
            print(f"Revoked contributor: {name} ({email})")
        else:
            print(f"No active contributor found with email {email}")


async def list_contributors() -> None:
    async with async_session() as session:
        result = await session.execute(
            select(Contributor).order_by(Contributor.created_at.desc())
        )
        contributors = result.scalars().all()
        if not contributors:
            print("No contributors found.")
            return
        for c in contributors:
            status = "DISABLED" if c.disabled_at else "active"
            print(f"  {c.token_prefix}... | {c.name:20s} | {c.email:30s} | {c.trust_tier:12s} | {status}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m scripts.manage_contributors <invite|revoke|list> [args]")
        print("  invite <name> <email> [tier] [invited_by]  — Invite a contributor")
        print("    tiers: admin, trusted, contributor (default)")
        print("  revoke <email>                              — Disable a contributor")
        print("  list                                        — List all contributors")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "invite":
        if len(sys.argv) < 4:
            print("Usage: python -m scripts.manage_contributors invite <name> <email> [tier] [invited_by]")
            sys.exit(1)
        name = sys.argv[2]
        email = sys.argv[3]
        tier = sys.argv[4] if len(sys.argv) > 4 else "contributor"
        invited_by = sys.argv[5] if len(sys.argv) > 5 else "justin"
        asyncio.run(invite(name, email, tier, invited_by))
    elif cmd == "revoke":
        if len(sys.argv) < 3:
            print("Usage: python -m scripts.manage_contributors revoke <email>")
            sys.exit(1)
        asyncio.run(revoke(sys.argv[2]))
    elif cmd == "list":
        asyncio.run(list_contributors())
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
