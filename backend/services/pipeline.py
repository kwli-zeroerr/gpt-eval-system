"""Pipeline service for running all modules in sequence."""
import logging
from typing import Dict, List, Optional, Callable, Awaitable
from schemas import GenerateRequest, QuestionItem
from services.generator import generate_questions
from services.question_logger import save_questions_to_log
from services.format_converter import convert_log_to_csv

logger = logging.getLogger(__name__)


async def run_full_pipeline(
    generate_request: GenerateRequest,
    progress_callback: Optional[Callable[[str, str, Dict], Awaitable[None]]] = None,
) -> Dict:
    """Run the full pipeline: question generation -> format conversion -> retrieval -> evaluation.
    
    Args:
        generate_request: Request for question generation
        progress_callback: Callback for progress updates (module, status, data)
    
    Returns:
        Dict with results from all modules
    """
    results = {
        "question_gen": None,
        "format_convert": None,
        "retrieval": None,
        "evaluation": None,
    }
    
    try:
        # Step 1: Question Generation
        if progress_callback:
            await progress_callback("question_gen", "start", {"message": "开始生成问题..."})
        
        req_id, questions, category_times, total_time = await generate_questions(
            categories=generate_request.categories,
            per_category=generate_request.per_category,
            prompt_overrides=generate_request.prompt_overrides,
            docs=generate_request.source_files,
        )
        
        # Save to log
        log_path = save_questions_to_log(req_id, questions, category_times, total_time)
        
        results["question_gen"] = {
            "request_id": req_id,
            "log_path": log_path,
            "total_questions": len(questions),
            "total_time": total_time,
        }
        
        if progress_callback:
            await progress_callback("question_gen", "complete", results["question_gen"])
        
        # Step 2: Format Conversion
        if progress_callback:
            await progress_callback("format_convert", "start", {"message": "开始转换格式..."})
        
        csv_path = convert_log_to_csv(log_path)
        
        results["format_convert"] = {
            "csv_path": csv_path,
            "log_path": log_path,
        }
        
        if progress_callback:
            await progress_callback("format_convert", "complete", results["format_convert"])
        
        # Step 3: Retrieval (placeholder for future implementation)
        if progress_callback:
            await progress_callback("retrieval", "start", {"message": "开始检索..."})
        
        # TODO: Implement retrieval module
        # For now, just mark as skipped
        results["retrieval"] = {
            "status": "skipped",
            "message": "检索模块待实现",
        }
        
        if progress_callback:
            await progress_callback("retrieval", "skipped", results["retrieval"])
        
        # Step 4: Evaluation (placeholder for future implementation)
        if progress_callback:
            await progress_callback("evaluation", "start", {"message": "开始评测..."})
        
        # TODO: Implement evaluation module
        # For now, just mark as skipped
        results["evaluation"] = {
            "status": "skipped",
            "message": "评测模块待实现",
        }
        
        if progress_callback:
            await progress_callback("evaluation", "skipped", results["evaluation"])
        
        return results
        
    except Exception as e:
        logger.error(f"Pipeline error: {e}")
        if progress_callback:
            await progress_callback("error", "error", {"message": str(e)})
        raise

