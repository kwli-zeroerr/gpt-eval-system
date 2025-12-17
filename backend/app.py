import uvicorn
import traceback
import logging
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse, FileResponse
import json
import asyncio
import pyfiglet

# Load environment variables from .env file
load_dotenv()

from schemas import CategoriesResponse, GenerateRequest, GenerateResponse, QuestionItem
from services.generator import generate_questions
from services.templates import DEFAULT_CATEGORIES
from services.question_logger import save_questions_to_log
from services.format_converter import convert_log_to_csv, list_log_files
from services.pipeline import run_full_pipeline
from typing import Dict, List
from fastapi.responses import FileResponse

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Print figlet banner on startup
figlet_text = pyfiglet.figlet_format("GPT-Evaluation", font="standard")
print("\n" + figlet_text)
print("=" * 60)
print("RAG Evaluation System - Backend Server")
print("=" * 60 + "\n")

app = FastAPI(title="Question Generator", version="0.1.0")


@app.get("/api/categories", response_model=CategoriesResponse)
async def list_categories():
    # Sort categories by ID (S1, S2, S3, S4, S5, S6)
    sorted_categories = sorted(DEFAULT_CATEGORIES, key=lambda c: c.id)
    return CategoriesResponse(categories=sorted_categories)


@app.post("/api/generate", response_model=GenerateResponse)
async def generate(req: GenerateRequest):
    try:
        logger.info(f"Generating questions for categories: {req.categories}, per_category: {req.per_category}")
        req_id, qs, category_times, total_time = await generate_questions(
            categories=req.categories,
            per_category=req.per_category,
            prompt_overrides=req.prompt_overrides,
            docs=req.source_files,
        )
        logger.info(f"Generated {len(qs)} questions in {total_time:.2f}s, request_id: {req_id}")
        
        # Save to log file with timing information
        try:
            log_path = save_questions_to_log(req_id, qs, category_times, total_time)
            logger.info(f"Questions saved to log: {log_path}")
        except Exception as log_err:
            logger.warning(f"Failed to save log: {log_err}")
        
        return GenerateResponse(request_id=req_id, questions=qs)
    except Exception as exc:
        error_msg = str(exc)
        error_trace = traceback.format_exc()
        logger.error(f"Error generating questions: {error_msg}\n{error_trace}")
        raise HTTPException(status_code=500, detail=error_msg)


@app.websocket("/ws/progress")
async def websocket_progress(websocket: WebSocket):
    """WebSocket endpoint for real-time progress updates."""
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_text()
            # Echo back or process progress updates
            await websocket.send_json({"status": "connected"})
    except WebSocketDisconnect:
        pass


async def generate_with_progress(req: GenerateRequest, websocket: WebSocket):
    """Generate questions with progress updates via WebSocket."""
    category_questions_map: Dict[str, List] = {}
    
    async def progress_callback(cat: str, current: int, total: int, elapsed: float = None):
        await websocket.send_json({
            "type": "progress",
            "category": cat,
            "current": current,
            "total": total,
            "percentage": int((current / total) * 100),
            "elapsed": elapsed
        })
    
    async def category_complete_callback(cat: str, questions: List[QuestionItem], elapsed: float):
        """Send questions for a category as soon as it's completed."""
        category_questions_map[cat] = questions
        await websocket.send_json({
            "type": "category_complete",
            "category": cat,
            "questions": [{"category": q.category, "text": q.text, "reference": q.reference or ""} for q in questions],
            "elapsed": elapsed
        })
    
    try:
        total_categories = len(req.categories)
        total_questions = total_categories * req.per_category
        
        await websocket.send_json({
            "type": "start",
            "total": total_questions,
            "categories": req.categories
        })
        
        req_id, qs, category_times, total_time = await generate_questions(
            categories=req.categories,
            per_category=req.per_category,
            prompt_overrides=req.prompt_overrides,
            docs=req.source_files,
            progress_callback=progress_callback,
            category_complete_callback=category_complete_callback
        )
        
        # Save to log with timing information
        try:
            log_path = save_questions_to_log(req_id, qs, category_times, total_time)
            await websocket.send_json({
                "type": "log_saved",
                "path": log_path
            })
        except Exception as log_err:
            logger.warning(f"Failed to save log: {log_err}")
        
        await websocket.send_json({
            "type": "complete",
            "request_id": req_id,
            "questions": [{"category": q.category, "text": q.text, "reference": q.reference} for q in qs],
            "total": len(qs),
            "category_times": category_times,
            "total_time": total_time
        })
    except Exception as exc:
        await websocket.send_json({
            "type": "error",
            "message": str(exc)
        })


@app.websocket("/ws/generate")
async def websocket_generate(websocket: WebSocket):
    """WebSocket endpoint for generating questions with progress."""
    await websocket.accept()
    try:
        # Receive request
        data = await websocket.receive_text()
        req_data = json.loads(data)
        req = GenerateRequest(**req_data)
        
        await generate_with_progress(req, websocket)
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        try:
            await websocket.send_json({
                "type": "error",
                "message": str(exc)
            })
        except:
            pass


@app.get("/api/format/logs")
async def list_question_logs():
    """List all available question log files."""
    logs = list_log_files()
    return {"logs": logs}


@app.post("/api/format/convert")
async def convert_to_csv(request: dict):
    """Convert a question log file to CSV format."""
    try:
        log_file_path = request.get("log_file_path")
        if not log_file_path:
            raise HTTPException(status_code=400, detail="log_file_path is required")
        csv_path = convert_log_to_csv(log_file_path)
        return {"csv_path": csv_path, "message": "Conversion successful"}
    except Exception as e:
        logger.error(f"Error converting to CSV: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/format/download/{log_id}")
async def download_csv(log_id: str):
    """Download CSV file for a log."""
    # Find log file by request_id
    logs = list_log_files()
    log_file = None
    for log in logs:
        if log["request_id"].startswith(log_id):
            # Convert to CSV
            csv_path = convert_log_to_csv(log["path"])
            return FileResponse(
                csv_path,
                media_type="text/csv",
                filename=f"questions_{log_id}.csv"
            )
    raise HTTPException(status_code=404, detail="Log file not found")


@app.websocket("/ws/pipeline")
async def websocket_pipeline(websocket: WebSocket):
    """WebSocket endpoint for running full pipeline."""
    await websocket.accept()
    try:
        # Receive request
        data = await websocket.receive_text()
        req_data = json.loads(data)
        req = GenerateRequest(**req_data)
        
        async def progress_callback(module: str, status: str, data: Dict):
            """Send progress updates via WebSocket."""
            await websocket.send_json({
                "type": "module_progress",
                "module": module,
                "status": status,
                "data": data,
            })
        
        # Run full pipeline
        results = await run_full_pipeline(req, progress_callback=progress_callback)
        
        # Send final results
        await websocket.send_json({
            "type": "complete",
            "results": results,
        })
        
    except WebSocketDisconnect:
        logger.info("WebSocket disconnected during pipeline")
    except Exception as exc:
        error_msg = str(exc)
        error_trace = traceback.format_exc()
        logger.error(f"Pipeline error: {error_msg}\n{error_trace}")
        try:
            await websocket.send_json({
                "type": "error",
                "message": error_msg,
            })
        except:
            pass


if __name__ == "__main__":  # pragma: no cover - manual run helper
    uvicorn.run("app:app", host="0.0.0.0", port=8180, reload=True)

