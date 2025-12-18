"""Format converter: Convert question logs to CSV format."""
import csv
import json
import re
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime
import os


# Category type mapping
CATEGORY_TYPES = {
    "S1": "S1 数值问答",
    "S2": "S2 报错信息问答",
    "S3": "S3 多选题",
    "S4": "S4 单文件多段",
    "S5": "S5 多文件多段",
    "S6": "S6 对抗数据/敏感信息",
}


def extract_theme_from_reference(reference: str) -> str:
    """Extract theme (document source) from reference.
    
    Examples:
    - "eRob用户手册.pdf" -> "eRob"
    - "eCoder用户手册.pdf" -> "eCoder"
    - "eRob用户手册.pdf:0" -> "eRob"
    """
    if not reference:
        return ""
    
    # Remove chunk index if present (e.g., "file.pdf:0" -> "file.pdf")
    filename = reference.split(":")[0]
    
    # Extract theme from filename
    # Common patterns: eRob, eCoder, etc.
    if "erob" in filename.lower():
        return "eRob"
    elif "ecoder" in filename.lower():
        return "eCoder"
    elif "手册" in filename or "manual" in filename.lower():
        # Try to extract product name before "手册" or "manual"
        match = re.search(r'([A-Za-z0-9]+).*?(?:手册|manual)', filename, re.IGNORECASE)
        if match:
            return match.group(1)
    
    # Fallback: use filename without extension
    name_without_ext = Path(filename).stem
    return name_without_ext.split("_")[0] if "_" in name_without_ext else name_without_ext


def extract_chapter_from_reference(reference: str) -> str:
    """Extract chapter information from reference.
    
    Currently returns the reference as-is. In the future, this could parse
    the document structure to extract actual chapter numbers like "1.1 关于本手册".
    
    For now, returns the filename or a placeholder.
    """
    if not reference:
        return ""
    
    # Remove chunk index if present
    filename = reference.split(":")[0]
    
    # TODO: Parse document structure to extract actual chapter numbers
    # For now, return a placeholder format
    # This would need integration with document parsing to get real chapter info
    return filename  # Placeholder: should be "1.1 关于本手册" format


def convert_log_to_csv(log_file_path: str, output_path: Optional[str] = None) -> str:
    """Convert question log JSON file to CSV format.
    
    CSV columns: question, answer, reference, type, theme
    
    Args:
        log_file_path: Path to JSON log file
        output_path: Optional output CSV path. If None, saves to data/ directory.
    
    Returns:
        Path to generated CSV file
    """
    log_path = Path(log_file_path)
    if not log_path.exists():
        raise FileNotFoundError(f"Log file not found: {log_file_path}")
    
    # Read JSON log
    with open(log_path, "r", encoding="utf-8") as f:
        log_data = json.load(f)
    
    # Generate output path - save to data/export/ directory
    if output_path is None:
        # Create data/export directory if it doesn't exist
        export_dir = Path("data/export")
        export_dir.mkdir(parents=True, exist_ok=True)
        output_path = export_dir / f"{log_path.stem}.csv"
    else:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Convert to CSV
    questions = log_data.get("questions", [])
    
    with open(output_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        # Write header
        writer.writerow(["question", "answer", "reference", "type", "theme"])
        
        # Write rows
        for q in questions:
            question = q.get("question", "")
            category = q.get("category", "")
            reference = q.get("reference", "")
            
            # Extract information
            question_type = CATEGORY_TYPES.get(category, category)
            theme = extract_theme_from_reference(reference)
            chapter_ref = extract_chapter_from_reference(reference)
            
            # answer is empty for now (to be filled later)
            # Escape commas and quotes in question text
            writer.writerow([question, "", chapter_ref, question_type, theme])
    
    return str(output_path)


def get_csv_path_for_log(log_file_path: str) -> str:
    """Get the expected CSV path for a log file without converting.
    
    Args:
        log_file_path: Path to JSON log file
    
    Returns:
        Expected CSV file path
    """
    log_path = Path(log_file_path)
    export_dir = Path("data/export")
    export_dir.mkdir(parents=True, exist_ok=True)
    return str(export_dir / f"{log_path.stem}.csv")


def csv_exists_for_log(log_file_path: str) -> bool:
    """Check if CSV file already exists for a log file.
    
    Args:
        log_file_path: Path to JSON log file
    
    Returns:
        True if CSV exists, False otherwise
    """
    csv_path = get_csv_path_for_log(log_file_path)
    return Path(csv_path).exists()


def list_log_files(log_dir: str = "data/frontend") -> List[Dict[str, str]]:
    """List all available log files from data/frontend/ directory.
    
    Returns:
        List of dicts with file info: {path, request_id, generated_at, total_questions}
    """
    log_path = Path(log_dir)
    if not log_path.exists():
        return []
    
    log_files = []
    for json_file in log_path.glob("questions_*.json"):
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            log_files.append({
                "path": str(json_file),
                "request_id": data.get("request_id", ""),
                "generated_at": data.get("generated_at", ""),
                "total_questions": data.get("total_questions", 0),
            })
        except Exception:
            continue
    
    # Sort by generated_at (newest first)
    log_files.sort(key=lambda x: x.get("generated_at", ""), reverse=True)
    return log_files


def delete_log_files(log_file_path: str) -> Dict[str, bool]:
    """Delete log files (JSON, TXT, CSV) for a given log file.
    
    Args:
        log_file_path: Path to JSON log file
    
    Returns:
        Dict with deletion status: {json_deleted, txt_deleted, csv_deleted}
    """
    log_path = Path(log_file_path)
    if not log_path.exists():
        return {"json_deleted": False, "txt_deleted": False, "csv_deleted": False}
    
    result = {"json_deleted": False, "txt_deleted": False, "csv_deleted": False}
    
    # Read request_id from JSON file
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            log_data = json.load(f)
        request_id = log_data.get("request_id", "")
    except Exception:
        request_id = ""
    
    # Delete JSON file
    try:
        if log_path.exists():
            log_path.unlink()
            result["json_deleted"] = True
    except Exception:
        pass
    
    # Delete corresponding TXT file from data/backend/
    if request_id:
        try:
            backend_dir = Path("data/backend")
            if backend_dir.exists():
                # Find TXT file with matching request_id
                for txt_file in backend_dir.glob("questions_*.txt"):
                    try:
                        with open(txt_file, "r", encoding="utf-8") as f:
                            content = f.read()
                            if request_id[:8] in content or request_id in content:
                                txt_file.unlink()
                                result["txt_deleted"] = True
                                break
                    except Exception:
                        continue
        except Exception:
            pass
    
    # Delete corresponding CSV file from data/export/
    try:
        csv_path = get_csv_path_for_log(log_file_path)
        csv_file = Path(csv_path)
        if csv_file.exists():
            csv_file.unlink()
            result["csv_deleted"] = True
    except Exception:
        pass
    
    return result


def list_csv_files(data_dir: str = "data/export") -> List[Dict[str, str]]:
    """List all available CSV files in data/export/ directory.
    
    Returns:
        List of dicts with file info: {path, filename, size, modified_at}
    """
    data_path = Path(data_dir)
    if not data_path.exists():
        return []
    
    csv_files = []
    for csv_file in data_path.glob("*.csv"):
        try:
            stat = csv_file.stat()
            csv_files.append({
                "path": str(csv_file),
                "filename": csv_file.name,
                "size": stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            })
        except Exception:
            continue
    
    # Sort by modified_at (newest first)
    csv_files.sort(key=lambda x: x.get("modified_at", ""), reverse=True)
    return csv_files

