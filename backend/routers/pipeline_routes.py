"""Full pipeline WebSocket route (question_gen -> format_convert -> retrieval -> evaluation)."""
import json
import logging
import traceback
from typing import Dict

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from schemas import GenerateRequest
from services.pipeline import run_full_pipeline

logger = logging.getLogger(__name__)

router = APIRouter()


@router.websocket("/ws/pipeline")
async def websocket_pipeline(websocket: WebSocket):
    """WebSocket endpoint for running full pipeline."""
    await websocket.accept()
    try:
        data = await websocket.receive_text()
        req_data = json.loads(data)
        req = GenerateRequest(**req_data)

        async def progress_callback(module: str, status: str, data: Dict):
            """Send progress updates via WebSocket."""
            await websocket.send_json(
                {
                    "type": "module_progress",
                    "module": module,
                    "status": status,
                    "data": data,
                }
            )

        results = await run_full_pipeline(req, progress_callback=progress_callback)

        await websocket.send_json({"type": "complete", "results": results})

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected during pipeline")
    except Exception as exc:  # noqa: BLE001
        error_msg = str(exc)
        error_trace = traceback.format_exc()
        logger.error("Pipeline error: %s\n%s", error_msg, error_trace)
        try:
            await websocket.send_json({"type": "error", "message": error_msg})
        except Exception:  # noqa: BLE001
            pass


