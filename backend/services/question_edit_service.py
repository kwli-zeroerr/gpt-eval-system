"""Service for editing questions: loading, validating, and saving edited questions."""
import json
import logging
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from datetime import datetime
from schemas import QuestionItem
from config.paths import DATA_FRONTEND_DIR
from services.question_logger import load_questions_from_log

logger = logging.getLogger(__name__)


def find_log_files_by_request_id(request_id: str) -> Tuple[Optional[Path], Optional[Path]]:
    """Find original and edited log files for a given request_id.
    
    Args:
        request_id: Request ID to search for
    
    Returns:
        Tuple of (original_path, edited_path). Either can be None if not found.
    """
    if not DATA_FRONTEND_DIR.exists():
        return None, None
    
    original_path = None
    edited_path = None
    
    # Search for files with matching request_id
    for json_file in DATA_FRONTEND_DIR.glob("questions_*.json"):
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            file_request_id = data.get("request_id", "")
            
            if file_request_id == request_id:
                if json_file.name.endswith("_edited.json"):
                    edited_path = json_file
                else:
                    original_path = json_file
        except Exception:
            continue
    
    return original_path, edited_path


def list_editable_logs() -> List[Dict[str, any]]:
    """List all available log files for editing.
    
    Returns:
        List of dicts with file info: {
            path, request_id, generated_at, total_questions, 
            is_edited, original_path
        }
    """
    if not DATA_FRONTEND_DIR.exists():
        return []
    
    log_files_map = {}  # request_id -> log_info
    
    # First pass: collect all files
    for json_file in DATA_FRONTEND_DIR.glob("questions_*.json"):
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            request_id = data.get("request_id", "")
            if not request_id:
                continue
            
            is_edited = json_file.name.endswith("_edited.json")
            
            # If this request_id is already in map, check if we should use edited version
            if request_id in log_files_map:
                existing = log_files_map[request_id]
                # If current file is edited and existing is not, replace
                if is_edited and not existing.get("is_edited", False):
                    log_files_map[request_id] = {
                        "path": str(json_file),
                        "request_id": request_id,
                        "generated_at": data.get("generated_at", ""),
                        "total_questions": data.get("total_questions", 0),
                        "is_edited": True,
                        "original_path": existing.get("path"),  # Keep original path
                    }
                # If both are edited or both are original, keep the newer one
                elif is_edited == existing.get("is_edited", False):
                    existing_time = existing.get("generated_at", "")
                    current_time = data.get("generated_at", "")
                    if current_time > existing_time:
                        log_files_map[request_id] = {
                            "path": str(json_file),
                            "request_id": request_id,
                            "generated_at": data.get("generated_at", ""),
                            "total_questions": data.get("total_questions", 0),
                            "is_edited": is_edited,
                            "original_path": existing.get("original_path") if is_edited else None,
                        }
            else:
                # First time seeing this request_id
                log_files_map[request_id] = {
                    "path": str(json_file),
                    "request_id": request_id,
                    "generated_at": data.get("generated_at", ""),
                    "total_questions": data.get("total_questions", 0),
                    "is_edited": is_edited,
                    "original_path": None,
                }
        except Exception as e:
            logger.warning(f"Error reading log file {json_file}: {e}")
            continue
    
    # Second pass: fill in original_path for edited files
    for request_id, log_info in log_files_map.items():
        if log_info.get("is_edited") and not log_info.get("original_path"):
            # Find original file
            original_path, _ = find_log_files_by_request_id(request_id)
            if original_path:
                log_info["original_path"] = str(original_path)
    
    # Convert to list and sort
    log_files = list(log_files_map.values())
    log_files.sort(key=lambda x: x.get("generated_at", ""), reverse=True)
    
    return log_files


def load_questions_for_edit(request_id: str, use_edited: bool = True) -> Tuple[List[QuestionItem], Dict[str, float], float, Dict]:
    """Load questions for editing.
    
    Args:
        request_id: Request ID to load
        use_edited: If True, prefer edited version; if False, only load original
    
    Returns:
        Tuple of (questions, category_times, total_time, metadata)
    """
    original_path, edited_path = find_log_files_by_request_id(request_id)
    
    # Determine which file to load
    load_path = None
    is_edited = False
    
    if use_edited and edited_path and edited_path.exists():
        load_path = edited_path
        is_edited = True
    elif original_path and original_path.exists():
        load_path = original_path
        is_edited = False
    else:
        raise FileNotFoundError(f"No log file found for request_id: {request_id}")
    
    # Load questions
    questions, category_times, total_time = load_questions_from_log(str(load_path))
    
    # Load metadata
    with open(load_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    metadata = {
        "request_id": request_id,
        "generated_at": data.get("generated_at", ""),
        "is_edited": is_edited,
        "original_path": str(original_path) if original_path else None,
        "edited_path": str(edited_path) if edited_path else None,
    }
    
    return questions, category_times, total_time, metadata


def validate_questions(questions: List[QuestionItem]) -> Tuple[bool, Optional[str]]:
    """Validate questions data.
    
    Args:
        questions: List of questions to validate
    
    Returns:
        Tuple of (is_valid, error_message)
    """
    if not questions:
        return False, "问题列表不能为空"
    
    valid_categories = {"S1", "S2", "S3", "S4", "S5", "S6"}
    
    for idx, q in enumerate(questions):
        # Check question text
        if not q.text or not q.text.strip():
            return False, f"第 {idx + 1} 个问题的文本不能为空"
        
        # Check category
        if q.category not in valid_categories:
            return False, f"第 {idx + 1} 个问题的类别无效: {q.category}（必须是 S1-S6 之一）"
    
    return True, None


def save_edited_questions(
    request_id: str,
    questions: List[QuestionItem],
    category_times: Optional[Dict[str, float]] = None,
    total_time: Optional[float] = None
) -> str:
    """Save edited questions to a new file (with _edited suffix).
    
    Args:
        request_id: Request ID
        questions: List of edited questions
        category_times: Optional category timing information
        total_time: Optional total time
    
    Returns:
        Path to saved file
    """
    # Validate questions
    is_valid, error_msg = validate_questions(questions)
    if not is_valid:
        raise ValueError(error_msg)
    
    # Find original file to get timestamp
    original_path, _ = find_log_files_by_request_id(request_id)
    
    if original_path:
        # Extract timestamp from original filename
        # Format: questions_YYYYMMDD_HHMMSS_request_id.json
        stem_parts = original_path.stem.split("_")
        if len(stem_parts) >= 3:
            timestamp = f"{stem_parts[1]}_{stem_parts[2]}"  # YYYYMMDD_HHMMSS
        else:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # Create edited file path
    edited_filename = f"questions_{timestamp}_{request_id[:8]}_edited.json"
    edited_path = DATA_FRONTEND_DIR / edited_filename
    
    # Ensure directory exists
    DATA_FRONTEND_DIR.mkdir(parents=True, exist_ok=True)
    
    # Prepare JSON data
    json_data = {
        "request_id": request_id,
        "generated_at": datetime.now().isoformat(),
        "total_questions": len(questions),
        "total_time": total_time,
        "category_times": category_times or {},
        "questions": [
            {
                "question": q.text,
                "category": q.category,
                "reference": q.reference or "",
            }
            for q in questions
        ]
    }
    
    # Write to file
    edited_path.write_text(
        json.dumps(json_data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    
    logger.info(f"Saved edited questions to: {edited_path}")
    return str(edited_path)


