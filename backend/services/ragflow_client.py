"""RAGFlow API client for retrieving chunks from knowledge base."""
import os
from typing import List, Optional
import httpx


class RAGFlowClient:
    """Client for accessing RAGFlow knowledge base via API."""

    def __init__(self):
        self.base_url = os.getenv("RAGFLOW_BASE_URL", "http://localhost:9380").rstrip("/")
        self.kb_id = os.getenv("RAGFLOW_KB_ID", "kb123")
        self.api_token = os.getenv("RAGFLOW_API_TOKEN", "")
        self.default_top_k = int(os.getenv("RAGFLOW_TOP_K", "10"))

    async def search_chunks(
        self,
        query: str,
        top_k: Optional[int] = None,
        use_ragflow_index: bool = True,
        use_rerank: bool = False,
    ) -> List[dict]:
        """Search chunks from knowledge base via RAGFlow API."""
        if not self.api_token:
            print("Warning: RAGFLOW_API_TOKEN not set, returning empty chunks")
            return []

        url = f"{self.base_url}/api/knowledge/{self.kb_id}/vector/search"
        headers = {"Authorization": f"Bearer {self.api_token}", "Content-Type": "application/json"}
        payload = {
            "query": query,
            "top_k": top_k or self.default_top_k,
            "use_ragflow_index": use_ragflow_index,
            "ragflow_format": True,
            "return_vector": False,
            "use_rerank": use_rerank,
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()
                return data.get("chunks", [])
        except httpx.HTTPError as e:
            print(f"Error calling RAGFlow API: {e}")
            return []

    async def get_context_snippets(
        self, query: str = "information", max_chunks: int = 3
    ) -> List[str]:
        """Get text snippets from knowledge base for prompt context."""
        chunks = await self.search_chunks(query, top_k=max_chunks)
        snippets = []
        for chunk in chunks[:max_chunks]:
            # Prefer content_with_weight, fallback to content_ltks or other fields
            content = chunk.get("content_with_weight") or chunk.get("content_ltks") or ""
            if content and content.strip():
                snippets.append(content.strip())
        return snippets

