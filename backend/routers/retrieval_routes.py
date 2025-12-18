"""Retrieval module routes."""
import logging
import os
import traceback
from typing import Dict

from fastapi import APIRouter, HTTPException

from services.retrieval_service import run_retrieval

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/api/retrieval/run")
async def run_retrieval_api(request: Dict):
    """Run retrieval module on a given CSV."""
    csv_path = request.get("csv_path")
    if not csv_path:
        raise HTTPException(status_code=400, detail="csv_path is required")
    try:
        ragflow_api_url = os.getenv("RAGFLOW_API_URL", "")
        ragflow_api_key = os.getenv("RAGFLOW_API_KEY", "")
        datasets_json_path = os.getenv("RAGFLOW_DATASETS_JSON", None)

        if not ragflow_api_url or not ragflow_api_key:
            raise HTTPException(status_code=400, detail="RagFlow API 配置未设置")

        retrieval_config = {
            "top_k": int(os.getenv("RAGFLOW_TOP_K", "5")),
            "similarity_threshold": float(os.getenv("RAGFLOW_SIMILARITY_THRESHOLD", "0.0")),
            "vector_similarity_weight": (
                float(os.getenv("RAGFLOW_VECTOR_SIMILARITY_WEIGHT", "0.3"))
                if os.getenv("RAGFLOW_VECTOR_SIMILARITY_WEIGHT")
                else None
            ),
        }

        result = await run_retrieval(
            csv_path=csv_path,
            ragflow_api_url=ragflow_api_url,
            ragflow_api_key=ragflow_api_key,
            retrieval_config=retrieval_config,
            datasets_json_path=datasets_json_path,
            max_workers=int(os.getenv("RAGFLOW_MAX_WORKERS", "1")),
            delay_between_requests=float(os.getenv("RAGFLOW_DELAY", "0.5")),
        )

        return result
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        error_msg = str(exc)
        error_trace = traceback.format_exc()
        logger.error("Retrieval error: %s\n%s", error_msg, error_trace)
        raise HTTPException(status_code=500, detail=error_msg)


