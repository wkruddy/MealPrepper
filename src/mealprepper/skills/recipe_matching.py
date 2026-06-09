from __future__ import annotations

from mealprepper.skills.pantry_config import _normalize_name

MIN_RECIPE_MATCH_SCORE = 200


def recipe_match_score(query: str, title: str) -> int:
    """Score how well a query matches a recipe title (higher is better)."""
    normalized_query = _normalize_name(query)
    normalized_title = _normalize_name(title)
    if not normalized_query or not normalized_title:
        return 0
    if normalized_query == normalized_title:
        return 1000
    if normalized_query in normalized_title or normalized_title in normalized_query:
        return 500 + len(normalized_query)
    query_tokens = set(normalized_query.split())
    title_tokens = set(normalized_title.split())
    overlap = query_tokens & title_tokens
    if not overlap:
        return 0

    unmatched_query = query_tokens - title_tokens
    if unmatched_query:
        # Multi-word queries must match every token to rank as a strong hit.
        return len(overlap) * 50

    return 300 + len(overlap) * 100
