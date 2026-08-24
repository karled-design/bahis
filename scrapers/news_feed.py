from __future__ import annotations

import json
import re
import threading
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from core.context_features import normalize_team_key, split_match_teams

__all__ = (
    "attach_experimental_news_to_matches",
    "get_news_feed_diag",
    "load_rss_sources",
)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_RSS_CONFIG_PATH = _PROJECT_ROOT / "config" / "experimental_rss.json"
_CACHE_TTL_SECONDS = 900
_FETCH_TIMEOUT = 8
_MAX_ITEMS_PER_FEED = 20
_MAX_AGE_HOURS = 24

_LOCK = threading.Lock()
_CACHE: dict[str, Any] = {"fetched_at": 0.0, "items": []}
_LAST_DIAG: dict[str, int] = {
    "feeds_loaded": 0,
    "items_total": 0,
    "items_matched": 0,
    "fetch_errors": 0,
}


def get_news_feed_diag() -> dict[str, int]:
    return dict(_LAST_DIAG)


def load_rss_sources() -> list[dict[str, str]]:
    if not _RSS_CONFIG_PATH.is_file():
        return []
    try:
        payload = json.loads(_RSS_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    sources = payload.get("sources")
    if not isinstance(sources, list):
        return []
    rows: list[dict[str, str]] = []
    for item in sources:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url", "")).strip()
        if not url:
            continue
        rows.append(
            {
                "id": str(item.get("id", url)).strip(),
                "label": str(item.get("label", "RSS")).strip(),
                "url": url,
            }
        )
    return rows


def _parse_pub_date(raw: str) -> datetime | None:
    value = raw.strip()
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text).strip()


def _fetch_rss_items(source: dict[str, str]) -> list[dict[str, Any]]:
    request = urllib.request.Request(
        source["url"],
        headers={"User-Agent": "SQE-V1-NewsFeed/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=_FETCH_TIMEOUT) as response:
            payload = response.read(500_000)
    except (urllib.error.URLError, TimeoutError, OSError):
        _LAST_DIAG["fetch_errors"] = int(_LAST_DIAG.get("fetch_errors", 0)) + 1
        return []

    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        _LAST_DIAG["fetch_errors"] = int(_LAST_DIAG.get("fetch_errors", 0)) + 1
        return []

    rows: list[dict[str, Any]] = []
    for item in root.findall(".//item"):
        title = _strip_html("".join(item.findtext("title") or ""))
        if not title:
            continue
        pub_raw = str(item.findtext("pubDate") or item.findtext("published") or "")
        published = _parse_pub_date(pub_raw)
        rows.append(
            {
                "source_id": source["id"],
                "source_label": source["label"],
                "title": title,
                "published_at": published.isoformat() if published else "",
            }
        )
        if len(rows) >= _MAX_ITEMS_PER_FEED:
            break
    return rows


def _refresh_cache_if_needed() -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc).timestamp()
    with _LOCK:
        age = now - float(_CACHE.get("fetched_at", 0.0) or 0.0)
        if age < _CACHE_TTL_SECONDS and isinstance(_CACHE.get("items"), list):
            return list(_CACHE["items"])

    sources = load_rss_sources()
    items: list[dict[str, Any]] = []
    for source in sources:
        items.extend(_fetch_rss_items(source))

    with _LOCK:
        _CACHE["fetched_at"] = now
        _CACHE["items"] = items
    _LAST_DIAG["feeds_loaded"] = len(sources)
    _LAST_DIAG["items_total"] = len(items)
    return items


def _item_team_keys(title: str) -> set[str]:
    keys: set[str] = set()
    home, away = split_match_teams(title)
    if home and away:
        keys.add(normalize_team_key(home))
        keys.add(normalize_team_key(away))
        return keys

    lowered = title.casefold()
    for token in re.split(r"[^a-z0-9]+", lowered):
        token = token.strip()
        if len(token) >= 4:
            keys.add(normalize_team_key(token))
    return keys


def _is_recent(published_at: str) -> bool:
    if not published_at:
        return True
    try:
        moment = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    age_hours = (datetime.now(timezone.utc) - moment.astimezone(timezone.utc)).total_seconds() / 3600.0
    return age_hours <= _MAX_AGE_HOURS


def _match_rss_hits(match: dict[str, Any], items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    match_name = str(match.get("match_name", "")).strip()
    home, away = split_match_teams(match_name)
    home_key = normalize_team_key(home)
    away_key = normalize_team_key(away)
    if not home_key or not away_key:
        return []

    hits: list[dict[str, Any]] = []
    for item in items:
        if not _is_recent(str(item.get("published_at", ""))):
            continue
        title = str(item.get("title", "")).strip()
        team_keys = _item_team_keys(title)
        if home_key not in team_keys and away_key not in team_keys:
            continue
        hits.append(
            {
                "title": title,
                "source": str(item.get("source_label", "")),
                "published_at": str(item.get("published_at", "")),
                "teams": sorted(team_keys),
            }
        )
    return hits[:5]


def attach_experimental_news_to_matches(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not matches:
        return matches

    items = _refresh_cache_if_needed()
    enriched: list[dict[str, Any]] = []
    matched_total = 0

    for match in matches:
        if not isinstance(match, dict):
            continue
        item = dict(match)
        bundle = item.get("context_bundle")
        injuries = bundle.get("injuries") if isinstance(bundle, dict) else None
        rss_hits = _match_rss_hits(item, items)
        if rss_hits:
            matched_total += 1
        news_payload: dict[str, Any] = {"rss_hits": rss_hits}
        if isinstance(injuries, dict):
            news_payload["injuries"] = injuries
        if rss_hits or isinstance(injuries, dict):
            item["experimental_news"] = news_payload
        enriched.append(item)

    _LAST_DIAG["items_matched"] = matched_total
    return enriched
