"""Fire-and-forget client to mirror crawled Paper rows into the research_assistant
Qdrant index. Mirrors the helper in `assistant-research-backend/public_api/services/`.
"""
import logging
import os

import requests
from django.utils import timezone

logger = logging.getLogger(__name__)

GLOBAL_USER_ID = "global"
DEFAULT_TIMEOUT = 10  # crawler runs offline, tolerate slower embedding calls


def _assistant_url() -> str:
    return os.environ.get("RESEARCH_ASSISTANT_URL", "http://research-assistant:8001").rstrip("/")


def _paper_payload(paper) -> dict:
    keywords = paper.keywords or []
    if isinstance(keywords, str):
        keywords = [k.strip() for k in keywords.split(",") if k.strip()]

    metadata = {
        "doi": getattr(paper, "doi", None),
        "url": getattr(paper, "url", None),
        "pdf_url": getattr(paper, "pdf_url", None),
        "github_url": getattr(paper, "github_url", None),
        "file_format": getattr(paper, "file_format", None),
        "citations_count": getattr(paper, "citations_count", None),
        "download_count": getattr(paper, "download_count", None),
        "views_count": getattr(paper, "views_count", None),
    }

    pub_date = getattr(paper, "publication_date", None)
    if pub_date:
        metadata["publication_date"] = pub_date.isoformat()
        metadata["publication_year"] = pub_date.year

    # Crawler's unmanaged Paper stub doesn't declare conference/journal FKs, so
    # these getattrs return None and the keys are dropped server-side.
    conference = getattr(paper, "conference", None)
    if conference is not None:
        metadata["conference_name"] = getattr(conference, "name", None)
    journal = getattr(paper, "journal", None)
    if journal is not None:
        metadata["journal_name"] = getattr(journal, "name", None)

    return {
        "paper_id": str(paper.id),
        "title": paper.title or "",
        "abstract": paper.abstract or "",
        "keywords": keywords,
        "user_id": GLOBAL_USER_ID,
        "metadata": metadata,
    }


def embed_paper(paper, *, timeout: int = DEFAULT_TIMEOUT) -> bool:
    try:
        resp = requests.post(
            f"{_assistant_url()}/papers",
            json=_paper_payload(paper),
            timeout=timeout,
        )
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("embed_paper failed for paper_id=%s: %s", paper.id, exc)
        return False

    try:
        paper.embedded_at = timezone.now()
        paper.save(update_fields=["embedded_at", "updated_at"])
    except Exception as exc:
        logger.warning("embed_paper persisted but couldn't update embedded_at for %s: %s", paper.id, exc)
    return True
