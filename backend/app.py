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
from services.retrieval_service import run_retrieval
from services.evaluation_service import run_evaluation
from typing import Dict, List
from fastapi.responses import FileResponse
import os

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Print figlet banner on startup
figlet_text = pyfiglet.figlet_format("GPT-Evaluation", font="standard")
print("\n" + figlet_text)
print("=" * 60)
print("GPT-Evaluation System - Backend Server")
print("=" * 60 + "\n")

app = FastAPI(title="GPT-Evaluation", version="0.1.0")


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


@app.get("/api/format/check-csv/{log_id}")
async def check_csv_exists(log_id: str):
    """Check if CSV file exists for a log."""
    logs = list_log_files()
    for log in logs:
        if log["request_id"].startswith(log_id):
            from services.format_converter import csv_exists_for_log
            exists = csv_exists_for_log(log["path"])
            csv_path = None
            if exists:
                from services.format_converter import get_csv_path_for_log
                csv_path = get_csv_path_for_log(log["path"])
            return {"exists": exists, "csv_path": csv_path}
    raise HTTPException(status_code=404, detail="Log file not found")


@app.delete("/api/format/logs/{log_id}")
async def delete_log(log_id: str):
    """Delete log files (JSON, TXT, CSV) for a given log ID."""
    from services.format_converter import delete_log_files
    from pathlib import Path
    
    # Find log file by request_id
    logs = list_log_files()
    log_file = None
    for log in logs:
        if log["request_id"].startswith(log_id):
            log_file = log
            break
    
    if not log_file:
        raise HTTPException(status_code=404, detail="Log file not found")
    
    try:
        result = delete_log_files(log_file["path"])
        return {
            "success": True,
            "message": "删除成功",
            "deleted": result
        }
    except Exception as e:
        logger.error(f"Error deleting log: {e}")
        raise HTTPException(status_code=500, detail=f"删除失败: {str(e)}")


@app.get("/api/format/download/{log_id}")
async def download_csv(log_id: str):
    """Download CSV file for a log. Converts if not exists."""
    # Find log file by request_id
    logs = list_log_files()
    log_file = None
    for log in logs:
        if log["request_id"].startswith(log_id):
            log_file = log
            break
    
    if not log_file:
        raise HTTPException(status_code=404, detail="Log file not found")
    
    # Check if CSV already exists
    from services.format_converter import csv_exists_for_log, get_csv_path_for_log
    if csv_exists_for_log(log_file["path"]):
        # CSV exists, return it directly
        csv_path = get_csv_path_for_log(log_file["path"])
    else:
        # CSV doesn't exist, convert first
        csv_path = convert_log_to_csv(log_file["path"])
    
    return FileResponse(
        csv_path,
        media_type="text/csv",
        filename=f"questions_{log_id}.csv"
    )


@app.get("/api/data/csv-files")
async def list_csv_files_api():
    """List all available CSV files in data/export/ directory (format conversion output)."""
    from services.format_converter import list_csv_files
    csv_files = list_csv_files()
    return {"csv_files": csv_files}


@app.get("/api/data/retrieval-csv-files")
async def list_retrieval_csv_files_api():
    """List all available CSV files in data/retrieval/ directory (retrieval output)."""
    from services.format_converter import list_csv_files
    csv_files = list_csv_files(data_dir="data/retrieval")
    return {"csv_files": csv_files}


@app.get("/api/evaluation/latest-summary")
async def get_latest_evaluation_summary():
    """Get the latest evaluation summary for dashboard from data/evaluation/."""
    from pathlib import Path
    import json
    
    evaluation_dir = Path("data/evaluation")
    if not evaluation_dir.exists():
        return {"summary": None}
    
    # Find the most recent evaluation summary in data/evaluation/
    summary_files = list(evaluation_dir.glob("**/evaluation_summary.json"))
    if not summary_files:
        return {"summary": None}
    
    # Get the most recent one
    latest_summary = max(summary_files, key=lambda p: p.stat().st_mtime)
    
    try:
        with open(latest_summary, "r", encoding="utf-8") as f:
            summary = json.load(f)
        return {"summary": summary}
    except Exception as e:
        logger.error(f"Error reading summary: {e}")
        return {"summary": None}


@app.post("/api/retrieval/run")
async def run_retrieval_api(request: dict):
    """运行检索模块"""
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
            "vector_similarity_weight": float(os.getenv("RAGFLOW_VECTOR_SIMILARITY_WEIGHT", "0.3")) if os.getenv("RAGFLOW_VECTOR_SIMILARITY_WEIGHT") else None,
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
    except Exception as exc:
        error_msg = str(exc)
        error_trace = traceback.format_exc()
        logger.error(f"Retrieval error: {error_msg}\n{error_trace}")
        raise HTTPException(status_code=500, detail=error_msg)


@app.post("/api/evaluation/run")
async def run_evaluation_api(request: dict):
    """运行评测模块"""
    csv_path = request.get("csv_path")
    if not csv_path:
        raise HTTPException(status_code=400, detail="csv_path is required")
    output_dir = request.get("output_dir")
    try:
        result = await run_evaluation(
            csv_path=csv_path,
            output_dir=output_dir,
        )
        return result
    except Exception as exc:
        error_msg = str(exc)
        error_trace = traceback.format_exc()
        logger.error(f"Evaluation error: {error_msg}\n{error_trace}")
        raise HTTPException(status_code=500, detail=error_msg)


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

