"""Format converter: Convert question logs to CSV format."""
import csv
import json
import re
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime


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
        output_path: Optional output CSV path. If None, uses same directory as log file.
    
    Returns:
        Path to generated CSV file
    """
    log_path = Path(log_file_path)
    if not log_path.exists():
        raise FileNotFoundError(f"Log file not found: {log_file_path}")
    
    # Read JSON log
    with open(log_path, "r", encoding="utf-8") as f:
        log_data = json.load(f)
    
    # Generate output path
    if output_path is None:
        output_path = log_path.parent / f"{log_path.stem}.csv"
    else:
        output_path = Path(output_path)
    
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


def list_log_files(log_dir: str = "logs") -> List[Dict[str, str]]:
    """List all available log files.
    
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

