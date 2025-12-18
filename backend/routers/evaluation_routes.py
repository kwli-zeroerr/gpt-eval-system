"""Evaluation module routes."""
import logging
import traceback
from typing import Dict

from fastapi import APIRouter, HTTPException

from services.evaluation_service import run_evaluation

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/api/evaluation/run")
async def run_evaluation_api(request: Dict):
    """Run evaluation module on a given CSV with answers."""
    csv_path = request.get("csv_path")
    if not csv_path:
        raise HTTPException(status_code=400, detail="csv_path is required")
    output_dir = request.get("output_dir")
    try:
        result = await run_evaluation(csv_path=csv_path, output_dir=output_dir)
        return result
    except Exception as exc:  # noqa: BLE001
        error_msg = str(exc)
        error_trace = traceback.format_exc()
        logger.error("Evaluation error: %s\n%s", error_msg, error_trace)
        raise HTTPException(status_code=500, detail=error_msg)


