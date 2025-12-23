"""Format converter: Convert question logs to CSV format."""
import csv
import json
import re
import logging
import sys
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime
import os
from config.paths import DATA_FRONTEND_DIR, DATA_EXPORT_DIR, DATA_RETRIEVAL_DIR, DATA_EVALUATION_DIR
from services.chapter_matcher import ChapterMatcher

# 增加 CSV 字段大小限制（默认 131072 字节，增加到 10MB）
csv.field_size_limit(min(sys.maxsize, 10 * 1024 * 1024))

logger = logging.getLogger(__name__)


# Category type mapping
CATEGORY_TYPES = {
    "S1": "S1 数值问答",
    "S2": "S2 定义问答",
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
    
    # 可能有多个 reference，用 ';' 分隔；每个元素结构为 "<dataset_pdf_name>|<chapter_info>" 或仅 "<dataset_pdf_name>"
    parts = [p.strip() for p in reference.split(";") if p.strip()]
    if not parts:
        return ""

    datasets = []
    for part in parts:
        dataset_part, sep, _chapter_part = part.partition("|")
        dataset_part = dataset_part.strip() or part
        datasets.append(dataset_part)

    # 不做省略，全部列出
    return ";".join(datasets)


def extract_chapter_from_reference(reference: str) -> str:
    """Extract chapter information from reference.
    
    Currently returns the reference as-is. In the future, this could parse
    the document structure to extract actual chapter numbers like "1.1 关于本手册".
    
    For now, returns the filename or a placeholder.
    """
    if not reference:
        return ""
    
    # 支持多个 reference，用 ';' 分隔；每个元素结构为 "<dataset>|<chapter_info>"
    refs = [p.strip() for p in reference.split(";") if p.strip()]
    if not refs:
        return ""

    chapters = []
    for ref in refs:
        dataset_part, sep, chapter_part = ref.partition("|")
        target = (chapter_part or dataset_part or ref).strip()

        # 优先从 chapter_part 中解析章节号，如 "13.2 配件" -> "13.2"
        chapter = ChapterMatcher.extract_chapter_info(target)
        if not chapter:
            # 兼容旧格式："file.pdf:0" 等
            filename = target.split(":")[0]
            chapter = ChapterMatcher.extract_chapter_info(filename) or filename

        chapters.append(chapter)

    # 不省略，全部章节一起返回
    return ";".join(chapters)


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
        export_dir = DATA_EXPORT_DIR
        export_dir.mkdir(parents=True, exist_ok=True)
        output_path = export_dir / f"{log_path.stem}.csv"
    else:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Convert to CSV
    questions = log_data.get("questions", [])
    
    with open(output_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        # Write header - 添加 answer_chapter 字段
        writer.writerow(["question", "answer", "answer_chapter", "reference", "type", "theme"])
        
        # Write rows
        for q in questions:
            question = q.get("question", "")
            category = q.get("category", "")
            raw_ref = (q.get("reference") or "").strip()
            
            # Extract information
            question_type = CATEGORY_TYPES.get(category, category)
            # 优先从 reference 提取 theme，如果 reference 为空则从 question 提取
            theme = extract_theme_from_reference(raw_ref) if raw_ref else ""
            # reference 应该以 theme 为主，从 reference 中提取章节信息
            # 如果 theme 存在，reference 应该只包含章节信息；如果 theme 不存在，reference 保持原样
            if theme:
                # 如果 theme 存在，reference 应该只包含章节部分
                chapter_ref = extract_chapter_from_reference(raw_ref)
            else:
                # 如果 theme 不存在，reference 保持原样（可能包含完整路径）
                chapter_ref = raw_ref
            
            # answer and answer_chapter are empty for now (to be filled later by retrieval module)
            writer.writerow([question, "", "", chapter_ref, question_type, theme])
    
    return str(output_path)


def get_csv_path_for_log(log_file_path: str) -> str:
    """Get the expected CSV path for a log file without converting.
    
    Args:
        log_file_path: Path to JSON log file
    
    Returns:
        Expected CSV file path
    """
    log_path = Path(log_file_path)
    export_dir = DATA_EXPORT_DIR
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


def list_log_files(log_dir: str = None) -> List[Dict[str, str]]:
    """List all available log files from data/frontend/ directory.
    
    Returns:
        List of dicts with file info: {path, request_id, generated_at, total_questions}
    """
    log_path = Path(log_dir) if log_dir is not None else DATA_FRONTEND_DIR
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
    
    result = {
        "json_deleted": False, 
        "txt_deleted": False, 
        "csv_deleted": False,
        "retrieval_files_deleted": 0,
        "evaluation_files_deleted": 0
    }
    
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
    
    # Delete corresponding CSV files from multiple directories
    # 1. Delete CSV from data/export/ (format conversion output)
    try:
        csv_path = get_csv_path_for_log(log_file_path)
        csv_file = Path(csv_path)
        if csv_file.exists():
            csv_file.unlink()
            result["csv_deleted"] = True
    except Exception:
        pass
    
    # 2. Delete CSV files from data/retrieval/ (retrieval output: *_with_answers.csv)
    # 检索结果文件命名格式：questions_YYYYMMDD_HHMMSS_<request_id_prefix>_with_answers.csv
    # 或者：<original_csv_stem>_with_answers.csv（如果原始 CSV 文件名包含 request_id）
    if request_id:
        try:
            retrieval_dir = DATA_RETRIEVAL_DIR
            if retrieval_dir.exists():
                deleted_retrieval_files = []
                # 方法1: 通过 request_id 前缀匹配（8位）
                request_id_prefix = request_id[:8]
                for csv_file in retrieval_dir.glob("*_with_answers.csv"):
                    # 检查文件名中是否包含 request_id 前缀
                    if request_id_prefix in csv_file.stem or request_id in csv_file.stem:
                        csv_file.unlink()
                        deleted_retrieval_files.append(str(csv_file))
                        logger.info(f"已删除检索 CSV 文件: {csv_file}")
                
                # 方法2: 通过原始 CSV 文件名匹配（如果原始 CSV 文件名包含 request_id）
                # 原始 CSV 文件命名格式：questions_YYYYMMDD_HHMMSS_<request_id>.csv
                # 检索结果文件：questions_YYYYMMDD_HHMMSS_<request_id>_with_answers.csv
                original_csv_stem = log_path.stem  # 例如：questions_20251219_111647_3ab13504
                matching_retrieval_file = retrieval_dir / f"{original_csv_stem}_with_answers.csv"
                if matching_retrieval_file.exists() and str(matching_retrieval_file) not in deleted_retrieval_files:
                    matching_retrieval_file.unlink()
                    deleted_retrieval_files.append(str(matching_retrieval_file))
                    logger.info(f"已删除检索 CSV 文件（通过原始文件名匹配）: {matching_retrieval_file}")
                
                if deleted_retrieval_files:
                    result["csv_deleted"] = True
                    result["retrieval_files_deleted"] = len(deleted_retrieval_files)
        except Exception as e:
            logger.warning(f"删除检索 CSV 文件失败: {e}")
    
    # 3. Delete CSV files from data/evaluation/ (evaluation output)
    if request_id:
        try:
            evaluation_dir = DATA_EVALUATION_DIR
            if evaluation_dir.exists():
                deleted_evaluation_files = []
                deleted_directories = []
                request_id_prefix = request_id[:8]
                
                # 删除所有匹配的 CSV 文件（递归查找子目录）
                for csv_file in evaluation_dir.glob("**/*.csv"):
                    # 检查文件名或路径中是否包含 request_id
                    if request_id_prefix in csv_file.stem or request_id in csv_file.stem or request_id_prefix in str(csv_file) or request_id in str(csv_file):
                        csv_file.unlink()
                        deleted_evaluation_files.append(str(csv_file))
                        logger.info(f"已删除评测 CSV 文件: {csv_file}")
                        
                        # 如果 CSV 文件在子目录中，记录目录以便后续删除
                        if csv_file.parent != evaluation_dir:
                            deleted_directories.append(csv_file.parent)
                
                # 删除 JSON 摘要文件（递归查找）
                for json_file in evaluation_dir.glob("**/evaluation_summary.json"):
                    try:
                        # 检查文件名或路径中是否包含 request_id
                        if request_id_prefix in json_file.stem or request_id in json_file.stem or request_id_prefix in str(json_file) or request_id in str(json_file):
                            json_file.unlink()
                            deleted_evaluation_files.append(str(json_file))
                            logger.info(f"已删除评测摘要文件: {json_file}")
                            
                            # 如果 JSON 文件在子目录中，记录目录以便后续删除
                            if json_file.parent != evaluation_dir:
                                deleted_directories.append(json_file.parent)
                        else:
                            # 如果路径不匹配，尝试读取 JSON 内容检查
                            with open(json_file, "r", encoding="utf-8") as f:
                                summary_data = json.load(f)
                                # 检查摘要中是否包含该 request_id
                                if request_id_prefix in str(summary_data) or request_id in str(summary_data):
                                    json_file.unlink()
                                    deleted_evaluation_files.append(str(json_file))
                                    logger.info(f"已删除评测摘要文件（通过内容匹配）: {json_file}")
                                    
                                    # 如果 JSON 文件在子目录中，记录目录以便后续删除
                                    if json_file.parent != evaluation_dir:
                                        deleted_directories.append(json_file.parent)
                    except Exception:
                        continue
                
                # 删除空的评测结果目录（如果目录中的所有文件都已删除）
                for eval_dir in set(deleted_directories):
                    try:
                        # 检查目录是否为空
                        if eval_dir.exists() and not any(eval_dir.iterdir()):
                            eval_dir.rmdir()
                            logger.info(f"已删除空的评测结果目录: {eval_dir}")
                    except Exception:
                        pass
                
                if deleted_evaluation_files:
                    result["csv_deleted"] = True
                    result["evaluation_files_deleted"] = len(deleted_evaluation_files)
        except Exception as e:
            logger.warning(f"删除评测 CSV 文件失败: {e}")
    
    return result


def list_csv_files(data_dir: str = None) -> List[Dict[str, str]]:
    """List all available CSV files in data/export/ directory.
    
    Returns:
        List of dicts with file info: {path, filename, size, modified_at}
    """
    data_path = Path(data_dir) if data_dir is not None else DATA_EXPORT_DIR
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

