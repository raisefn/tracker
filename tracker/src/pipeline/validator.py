from datetime import date

from src.collectors.base import RawRound

BITCOIN_GENESIS = date(2009, 1, 3)


def validate_round(raw: RawRound) -> list[str]:
    """Validate a raw round. Returns list of failure reasons (empty = valid)."""
    failures: list[str] = []

    # Amount sanity
    if raw.amount_usd is not None:
        if raw.amount_usd < 10_000:
            failures.append(f"Amount too small: ${raw.amount_usd}")
        if raw.amount_usd > 10_000_000_000:
            failures.append(f"Amount too large: ${raw.amount_usd}")

    # Date sanity
    if raw.date > date.today():
        failures.append(f"Date in the future: {raw.date}")
    if raw.date < BITCOIN_GENESIS:
        failures.append(f"Date before Bitcoin genesis: {raw.date}")

    # Investor sanity
    all_investors = raw.lead_investors + raw.other_investors
    if not all_investors:
        failures.append("No investors listed")

    # Project name
    if not raw.project_name or raw.project_name.strip() == "":
        failures.append("Missing project name")

    return failures


def compute_confidence(raw: RawRound, source_type: str, validation_failures: list[str]) -> float:
    """Compute confidence score (0.0 - 1.0) for a round."""
    score = 0.5  # base

    # Source bonus
    source_scores = {
        "defillama": 0.3,
        "sec_edgar": 0.3,
        "news": 0.1,
        "community": 0.0,
        "manual": 0.0,
    }
    score += source_scores.get(source_type, 0.0)

    # Penalties for validation failures
    score -= len(validation_failures) * 0.15

    # Bonus for having lead investors
    if raw.lead_investors:
        score += 0.05

    # Bonus for having amount
    if raw.amount_usd:
        score += 0.05

    # Bonus for source URL
    if raw.source_url:
        score += 0.05

    # Age penalty
    days_old = (date.today() - raw.date).days
    years_old = days_old / 365.25
    score -= years_old * 0.02

    return max(0.0, min(1.0, score))
