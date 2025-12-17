"""Logger for saving generated questions in both human-readable and JSON format."""
import os
import json
from datetime import datetime
from pathlib import Path
from typing import List, Dict
from schemas import QuestionItem


def save_questions_to_log(
    request_id: str, 
    questions: List[QuestionItem],
    category_times: Dict[str, float] = None,
    total_time: float = None
) -> str:
    """Save questions to log file in both human-readable and JSON format.
    
    Returns the path to the JSON log file.
    """
    # Create logs directory if it doesn't exist
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    
    # Group questions by category
    questions_by_category: dict[str, List[QuestionItem]] = {}
    for q in questions:
        if q.category not in questions_by_category:
            questions_by_category[q.category] = []
        questions_by_category[q.category].append(q)
    
    # Human-readable format (for backward compatibility)
    log_lines = []
    log_lines.append(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log_lines.append(f"请求ID: {request_id}")
    log_lines.append(f"总问题数: {len(questions)}")
    if total_time is not None:
        log_lines.append(f"总耗时: {total_time:.2f}秒")
    log_lines.append("=" * 80)
    log_lines.append("")
    
    for cat_id in sorted(questions_by_category.keys()):
        cat_questions = questions_by_category[cat_id]
        cat_time_str = ""
        if category_times and cat_id in category_times:
            cat_time_str = f" (耗时: {category_times[cat_id]:.2f}秒)"
        log_lines.append(f"{cat_id}定义{cat_time_str}：")
        for idx, q in enumerate(cat_questions, start=1):
            ref_info = f" [来源: {q.reference}]" if q.reference else ""
            log_lines.append(f"{idx}. {q.text}{ref_info}")
        log_lines.append("")
    
    # Write human-readable file
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    txt_file = log_dir / f"questions_{timestamp}_{request_id[:8]}.txt"
    txt_file.write_text("\n".join(log_lines), encoding="utf-8")
    
    # Write JSON file (for format conversion module)
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
    json_file = log_dir / f"questions_{timestamp}_{request_id[:8]}.json"
    json_file.write_text(json.dumps(json_data, ensure_ascii=False, indent=2), encoding="utf-8")
    
    return str(json_file)

