"""Format conversion and CSV/log management routes."""
import logging
from pathlib import Path
from typing import Dict, List

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from services.format_converter import (
    convert_log_to_csv,
    list_log_files,
    list_csv_files,
    csv_exists_for_log,
    get_csv_path_for_log,
    delete_log_files,
)
from config.paths import DATA_RETRIEVAL_DIR, DATA_EVALUATION_DIR

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/format/logs")
async def list_question_logs():
    """List all available question log files."""
    logs = list_log_files()
    return {"logs": logs}


@router.post("/api/format/convert")
async def convert_to_csv(request: Dict):
    """Convert a question log file to CSV format."""
    try:
        log_file_path = request.get("log_file_path")
        if not log_file_path:
            raise HTTPException(status_code=400, detail="log_file_path is required")
        csv_path = convert_log_to_csv(log_file_path)
        return {"csv_path": csv_path, "message": "Conversion successful"}
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error("Error converting to CSV: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/api/format/check-csv/{log_id}")
async def check_csv_exists(log_id: str):
    """Check if CSV file exists for a log."""
    logs = list_log_files()
    for log in logs:
        if log["request_id"].startswith(log_id):
            exists = csv_exists_for_log(log["path"])
            csv_path = None
            if exists:
                csv_path = get_csv_path_for_log(log["path"])
            return {"exists": exists, "csv_path": csv_path}
    raise HTTPException(status_code=404, detail="Log file not found")


@router.delete("/api/format/logs/{log_id}")
async def delete_log(log_id: str):
    """Delete log files (JSON, TXT, CSV) for a given log ID."""
    logs = list_log_files()
    log_file = next((log for log in logs if log["request_id"].startswith(log_id)), None)

    if not log_file:
        raise HTTPException(status_code=404, detail="Log file not found")

    try:
        result = delete_log_files(log_file["path"])
        return {"success": True, "message": "删除成功", "deleted": result}
    except Exception as exc:  # noqa: BLE001
        logger.error("Error deleting log: %s", exc)
        raise HTTPException(status_code=500, detail=f"删除失败: {str(exc)}")


@router.get("/api/format/download/{log_id}")
async def download_csv(log_id: str):
    """Download CSV file for a log. Converts if not exists."""
    logs = list_log_files()
    log_file = next((log for log in logs if log["request_id"].startswith(log_id)), None)

    if not log_file:
        raise HTTPException(status_code=404, detail="Log file not found")

    # Check if CSV already exists
    if csv_exists_for_log(log_file["path"]):
        csv_path = get_csv_path_for_log(log_file["path"])
    else:
        csv_path = convert_log_to_csv(log_file["path"])

    return FileResponse(
        csv_path,
        media_type="text/csv",
        filename=f"questions_{log_id}.csv",
    )


@router.get("/api/data/csv-files")
async def list_csv_files_api():
    """List all available CSV files in data/export/ directory (format conversion output)."""
    csv_files = list_csv_files()
    return {"csv_files": csv_files}


@router.get("/api/data/retrieval-csv-files")
async def list_retrieval_csv_files_api():
    """List all available CSV files in data/retrieval/ directory (retrieval output)."""
    csv_files = list_csv_files(data_dir=str(DATA_RETRIEVAL_DIR))
    return {"csv_files": csv_files}


@router.get("/api/evaluation/latest-summary")
async def get_latest_evaluation_summary():
    """Get the latest evaluation summary for dashboard from data/evaluation/."""
    evaluation_dir = DATA_EVALUATION_DIR
    if not evaluation_dir.exists():
        return {"summary": None}

    summary_files: List[Path] = list(evaluation_dir.glob("**/evaluation_summary.json"))
    if not summary_files:
        return {"summary": None}

    latest_summary = max(summary_files, key=lambda p: p.stat().st_mtime)

    try:
        import json as _json

        with latest_summary.open("r", encoding="utf-8") as f:
            summary = _json.load(f)
        return {"summary": summary}
    except Exception as exc:  # noqa: BLE001
        logger.error("Error reading summary: %s", exc)
        return {"summary": None}

