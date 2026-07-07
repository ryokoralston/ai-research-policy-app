"""Federal Register REST API client for tracking regulatory/rulemaking activity.

Mirrors the shape of tavily_client.py: a small dataclass plus a client class
with an async search() method built on httpx.AsyncClient. No API key required.

API docs: https://www.federalregister.gov/developers/documentation/api/v1
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)


@dataclass
class RegulatoryDocument:
    document_number: str
    title: str
    abstract: str | None
    html_url: str
    publication_date: str
    document_type: str
    agencies: list[str]


class FederalRegisterClient:
    BASE_URL = "https://www.federalregister.gov/api/v1"

    async def search(
        self,
        query: str,
        published_after: str | None = None,
        max_results: int = 5,
    ) -> list[RegulatoryDocument]:
        params = {
            "conditions[term]": query,
            "per_page": max_results,
            "order": "newest",
        }
        if published_after:
            params["conditions[publication_date][gte]"] = published_after

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(f"{self.BASE_URL}/documents.json", params=params)
            resp.raise_for_status()
            data = resp.json()

        results = []
        for item in data.get("results", []):
            agencies = [
                a.get("name", "") for a in item.get("agencies", []) if a.get("name")
            ]
            results.append(RegulatoryDocument(
                document_number=item.get("document_number", ""),
                title=item.get("title", ""),
                abstract=item.get("abstract"),
                html_url=item.get("html_url", ""),
                publication_date=item.get("publication_date", ""),
                document_type=item.get("type", ""),
                agencies=agencies,
            ))
        return results


async def fetch_top_regulatory_documents(
    topics: list[str],
    max_total: int = 5,
    published_after: str | None = None,
) -> list[RegulatoryDocument]:
    """Search all topics and return deduplicated top documents sorted by date."""
    client = FederalRegisterClient()
    seen_numbers: set[str] = set()
    all_results: list[RegulatoryDocument] = []

    for topic in topics:
        try:
            results = await client.search(
                query=topic,
                published_after=published_after,
                max_results=5,
            )
            for doc in results:
                if doc.document_number not in seen_numbers:
                    seen_numbers.add(doc.document_number)
                    all_results.append(doc)
        except Exception:
            logger.exception("Federal Register search failed for topic: %s", topic)

    all_results.sort(key=lambda d: d.publication_date, reverse=True)
    return all_results[:max_total]
