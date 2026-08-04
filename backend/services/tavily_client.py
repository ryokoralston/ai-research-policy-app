"""Tavily search API client."""
import httpx
from dataclasses import dataclass

from config import get_settings
from services.usage import record_tavily


@dataclass
class SearchResult:
    url: str
    title: str
    snippet: str
    content: str | None
    score: float
    published_date: str | None


class TavilyClient:
    BASE_URL = "https://api.tavily.com"

    def __init__(self):
        self.api_key = get_settings().tavily_api_key

    async def search(
        self,
        query: str,
        max_results: int = 5,
        include_raw_content: bool = True,
        search_depth: str = "advanced",
    ) -> list[SearchResult]:
        payload = {
            "api_key": self.api_key,
            "query": query,
            "max_results": max_results,
            "include_raw_content": include_raw_content,
            "search_depth": search_depth,
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(f"{self.BASE_URL}/search", json=payload)
            resp.raise_for_status()
            data = resp.json()

        # Billed per search, so one row per successful call — recorded after
        # raise_for_status so failed requests aren't counted as spend.
        record_tavily(1)

        results = []
        for item in data.get("results", []):
            results.append(SearchResult(
                url=item.get("url", ""),
                title=item.get("title", ""),
                snippet=item.get("content", ""),
                content=item.get("raw_content"),
                score=item.get("score", 0.0),
                published_date=item.get("published_date"),
            ))
        return results
