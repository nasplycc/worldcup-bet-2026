"""Small helpers for user profile match lists.

The profile payload is stored in UserPreference.watchlist so we can add a
proper table later without changing the frontend contract.
"""


def normalize_profile_items(value, limit=80):
    if not isinstance(value, list):
        return []

    result = []
    seen = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        league = str(item.get("league") or "").strip()
        match_id = str(item.get("id") or "").strip()
        key = f"{league}:{match_id}"
        if not league or not match_id or key in seen:
            continue
        seen.add(key)
        result.append({
            "league": league,
            "id": match_id,
            "title": str(item.get("title") or "")[:160],
            "sub": str(item.get("sub") or "")[:160],
            "savedAt": str(item.get("savedAt") or item.get("viewedAt") or ""),
            "viewedAt": str(item.get("viewedAt") or item.get("savedAt") or ""),
        })
        if len(result) >= limit:
            break
    return result


def preference_profile_payload(pref):
    raw = pref.watchlist if pref and isinstance(pref.watchlist, (dict, list)) else {}
    if isinstance(raw, list):
        raw = {"favorites": raw, "history": []}
    return {
        "favorites": normalize_profile_items(raw.get("favorites"), limit=120),
        "history": normalize_profile_items(raw.get("history"), limit=120),
    }
