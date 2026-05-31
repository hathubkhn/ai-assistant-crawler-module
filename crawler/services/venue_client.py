"""Call backend auto venue mapping after crawl ingest."""
import logging
import os

import requests

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 120


def _backend_base() -> str:
    return os.environ.get("BACKEND_API_URL", "http://localhost:8000").rstrip("/")


def map_paper_venue(paper_id, *, timeout: int = DEFAULT_TIMEOUT) -> dict | None:
    """
    POST /api/papers/<id>/map-venue/ on the Django backend.
    Never raises; returns parsed JSON or None on failure.
    """
    url = f"{_backend_base()}/api/papers/{paper_id}/map-venue/"
    headers = {}
    key = os.environ.get("INTERNAL_VENUE_MAP_KEY", "")
    if key:
        headers["X-Internal-Key"] = key

    try:
        resp = requests.post(url, headers=headers, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.warning("map_paper_venue failed for paper_id=%s: %s", paper_id, exc)
        return None
