"""RagFlow integration status endpoints."""
import logging
import os
from typing import Dict, Any

from fastapi import APIRouter

from services.ragflow_client import RagFlowClient

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/ragflow/status")
async def ragflow_status() -> Dict[str, Any]:
    """Check RagFlow connectivity and local datasets.json configuration."""
    api_url = os.getenv("RAGFLOW_API_URL", "").rstrip("/")
    api_key = os.getenv("RAGFLOW_API_KEY", "")
    datasets_json_path = os.getenv("RAGFLOW_DATASETS_JSON", "datasets.json")

    configured = bool(api_url and api_key)

    status: Dict[str, Any] = {
        "ragflow": {
            "configured": configured,
            "api_url": api_url or None,
        },
        "datasets": {
            "config_path": datasets_json_path,
            "dataset_count": 0,
            "document_count": 0,
        },
    }

    if not configured:
        status["ragflow"]["reachable"] = False
        status["ragflow"]["message"] = "RAGFLOW_API_URL 或 RAGFLOW_API_KEY 未配置"
        return status

    client = RagFlowClient(api_url, api_key)

    # Check connectivity by listing datasets (first page)
    try:
        ds_result = client.list_datasets(page=1, page_size=1)
        error = ds_result.get("error")
        if error:
            status["ragflow"]["reachable"] = False
            status["ragflow"]["message"] = f"连接失败: {error}"
        else:
            status["ragflow"]["reachable"] = True
            status["ragflow"]["message"] = "连接成功"
    except Exception as exc:  # noqa: BLE001
        logger.warning("RagFlow connectivity check failed: %s", exc)
        status["ragflow"]["reachable"] = False
        status["ragflow"]["message"] = f"连接异常: {exc}"

    # Load local datasets.json and count IDs
    try:
        dataset_ids, document_ids = client.get_all_datasets_and_documents(datasets_json_path)
        status["datasets"]["dataset_count"] = len(set(dataset_ids))
        status["datasets"]["document_count"] = len(set(document_ids))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to load datasets.json: %s", exc)
        status["datasets"]["message"] = f"加载失败: {exc}"

    return status


