"""API routes for question editing."""
import logging
from fastapi import APIRouter, HTTPException
from typing import List, Dict, Optional
from schemas import QuestionItem
from services.question_edit_service import (
    list_editable_logs,
    load_questions_for_edit,
    save_edited_questions,
    validate_questions,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/questions/edit/logs")
async def get_editable_logs():
    """Get list of all editable log files."""
    try:
        logs = list_editable_logs()
        return {"logs": logs}
    except Exception as exc:
        error_msg = str(exc)
        logger.error("Error listing editable logs: %s", error_msg)
        raise HTTPException(status_code=500, detail=error_msg)


@router.get("/api/questions/edit/{request_id}")
async def get_questions_for_edit(request_id: str, use_edited: bool = True):
    """Load questions for editing.
    
    Args:
        request_id: Request ID to load
        use_edited: If True, prefer edited version; if False, only load original
    """
    try:
        questions, category_times, total_time, metadata = load_questions_for_edit(
            request_id, use_edited=use_edited
        )
        
        return {
            "request_id": request_id,
            "questions": [
                {
                    "category": q.category,
                    "text": q.text,
                    "reference": q.reference or "",
                }
                for q in questions
            ],
            "category_times": category_times,
            "total_time": total_time,
            "metadata": metadata,
        }
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        error_msg = str(exc)
        logger.error("Error loading questions for edit: %s", error_msg)
        raise HTTPException(status_code=500, detail=error_msg)


@router.put("/api/questions/edit/{request_id}")
async def update_questions(request_id: str, data: Dict):
    """Update questions (validate only, does not save).
    
    Request body:
    {
        "questions": [{"category": "S1", "text": "...", "reference": "..."}],
        "category_times": {...},
        "total_time": 123.45
    }
    """
    try:
        questions_data = data.get("questions", [])
        questions = [
            QuestionItem(
                category=q.get("category", ""),
                text=q.get("text", ""),
                reference=q.get("reference", ""),
            )
            for q in questions_data
        ]
        
        # Validate
        is_valid, error_msg = validate_questions(questions)
        if not is_valid:
            raise HTTPException(status_code=400, detail=error_msg)
        
        return {
            "request_id": request_id,
            "valid": True,
            "total_questions": len(questions),
        }
    except HTTPException:
        raise
    except Exception as exc:
        error_msg = str(exc)
        logger.error("Error validating questions: %s", error_msg)
        raise HTTPException(status_code=500, detail=error_msg)


@router.post("/api/questions/edit/{request_id}/save")
async def save_questions(request_id: str, data: Dict):
    """Save edited questions.
    
    Request body:
    {
        "questions": [{"category": "S1", "text": "...", "reference": "..."}],
        "category_times": {...},
        "total_time": 123.45
    }
    """
    try:
        questions_data = data.get("questions", [])
        questions = [
            QuestionItem(
                category=q.get("category", ""),
                text=q.get("text", ""),
                reference=q.get("reference", ""),
            )
            for q in questions_data
        ]
        
        category_times = data.get("category_times", {})
        total_time = data.get("total_time")
        
        # Save
        saved_path = save_edited_questions(
            request_id=request_id,
            questions=questions,
            category_times=category_times,
            total_time=total_time,
        )
        
        return {
            "request_id": request_id,
            "saved_path": saved_path,
            "total_questions": len(questions),
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        error_msg = str(exc)
        logger.error("Error saving edited questions: %s", error_msg)
        raise HTTPException(status_code=500, detail=error_msg)


