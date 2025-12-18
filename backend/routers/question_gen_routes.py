"""Question generation and related WebSocket routes."""
import json
import logging
import traceback
from typing import Dict, List

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from schemas import CategoriesResponse, GenerateRequest, GenerateResponse, QuestionItem
from services.generator import generate_questions
from services.templates import DEFAULT_CATEGORIES
from services.question_logger import save_questions_to_log

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/categories", response_model=CategoriesResponse)
async def list_categories():
    """List all question categories (S1-S6) in a stable order."""
    sorted_categories = sorted(DEFAULT_CATEGORIES, key=lambda c: c.id)
    return CategoriesResponse(categories=sorted_categories)


@router.post("/api/generate", response_model=GenerateResponse)
async def generate(req: GenerateRequest):
    """HTTP endpoint for one-shot question generation (no streaming progress)."""
    try:
        logger.info(
            "Generating questions for categories: %s, per_category: %s",
            req.categories,
            req.per_category,
        )
        req_id, qs, category_times, total_time = await generate_questions(
            categories=req.categories,
            per_category=req.per_category,
            prompt_overrides=req.prompt_overrides,
            docs=req.source_files,
        )
        logger.info(
            "Generated %d questions in %.2fs, request_id: %s",
            len(qs),
            total_time,
            req_id,
        )

        # Save to log file with timing information
        try:
            log_path = save_questions_to_log(req_id, qs, category_times, total_time)
            logger.info("Questions saved to log: %s", log_path)
        except Exception as log_err:  # noqa: BLE001
            logger.warning("Failed to save log: %s", log_err)

        return GenerateResponse(request_id=req_id, questions=qs)
    except Exception as exc:  # noqa: BLE001
        error_msg = str(exc)
        error_trace = traceback.format_exc()
        logger.error("Error generating questions: %s\n%s", error_msg, error_trace)
        raise HTTPException(status_code=500, detail=error_msg)


@router.websocket("/ws/progress")
async def websocket_progress(websocket: WebSocket):
    """Legacy WebSocket endpoint for simple connectivity check / progress echo."""
    await websocket.accept()
    try:
        while True:
            await websocket.receive_text()
            await websocket.send_json({"status": "connected"})
    except WebSocketDisconnect:
        # Client disconnected, nothing special to do
        pass


async def generate_with_progress(req: GenerateRequest, websocket: WebSocket):
    """Generate questions with fine-grained progress updates via WebSocket."""
    category_questions_map: Dict[str, List[QuestionItem]] = {}

    async def progress_callback(cat: str, current: int, total: int, elapsed: float | None = None):
        await websocket.send_json(
            {
                "type": "progress",
                "category": cat,
                "current": current,
                "total": total,
                "percentage": int((current / total) * 100),
                "elapsed": elapsed,
            }
        )

    async def category_complete_callback(
        cat: str, questions: List[QuestionItem], elapsed: float
    ):
        """Send questions for a category as soon as it's completed."""
        category_questions_map[cat] = questions
        await websocket.send_json(
            {
                "type": "category_complete",
                "category": cat,
                "questions": [
                    {
                        "category": q.category,
                        "text": q.text,
                        "reference": q.reference or "",
                    }
                    for q in questions
                ],
                "elapsed": elapsed,
            }
        )

    try:
        total_categories = len(req.categories)
        total_questions = total_categories * req.per_category

        await websocket.send_json(
            {
                "type": "start",
                "total": total_questions,
                "categories": req.categories,
            }
        )

        req_id, qs, category_times, total_time = await generate_questions(
            categories=req.categories,
            per_category=req.per_category,
            prompt_overrides=req.prompt_overrides,
            docs=req.source_files,
            progress_callback=progress_callback,
            category_complete_callback=category_complete_callback,
        )

        # Save to log with timing information
        try:
            log_path = save_questions_to_log(req_id, qs, category_times, total_time)
            await websocket.send_json({"type": "log_saved", "path": log_path})
        except Exception as log_err:  # noqa: BLE001
            logger.warning("Failed to save log: %s", log_err)

        await websocket.send_json(
            {
                "type": "complete",
                "request_id": req_id,
                "questions": [
                    {
                        "category": q.category,
                        "text": q.text,
                        "reference": q.reference,
                    }
                    for q in qs
                ],
                "total": len(qs),
                "category_times": category_times,
                "total_time": total_time,
            }
        )
    except Exception as exc:  # noqa: BLE001
        await websocket.send_json({"type": "error", "message": str(exc)})


@router.websocket("/ws/generate")
async def websocket_generate(websocket: WebSocket):
    """WebSocket endpoint for generating questions with real-time progress."""
    await websocket.accept()
    try:
        data = await websocket.receive_text()
        req_data = json.loads(data)
        req = GenerateRequest(**req_data)
        await generate_with_progress(req, websocket)
    except WebSocketDisconnect:
        # Client disconnected during generation
        pass
    except Exception as exc:  # noqa: BLE001
        try:
            await websocket.send_json({"type": "error", "message": str(exc)})
        except Exception:  # noqa: BLE001
            # If sending fails there's nothing more we can do
            pass


