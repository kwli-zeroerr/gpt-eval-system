"""Pipeline service for running all modules in sequence."""
import logging
import os
from typing import Dict, List, Optional, Callable, Awaitable
from schemas import GenerateRequest, QuestionItem
from services.generator import generate_questions
from services.question_logger import save_questions_to_log
from services.format_converter import convert_log_to_csv
from services.retrieval_service import run_retrieval
from services.evaluation_service import run_evaluation

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
        
        async def qg_category_progress(cat: str, current: int, total: int, elapsed: float = None):
            """Callback for category-level progress"""
            if progress_callback:
                await progress_callback("question_gen", "progress", {
                    "category": cat,
                    "current": current,
                    "total": total,
                    "elapsed": elapsed,
                })
        
        async def qg_category_complete(cat: str, questions: List[QuestionItem], elapsed: float):
            """Callback when a category is completed"""
            if progress_callback:
                await progress_callback("question_gen", "category_complete", {
                    "category": cat,
                    "count": len(questions),
                    "elapsed": elapsed,
                })
        
        req_id, questions, category_times, total_time = await generate_questions(
            categories=generate_request.categories,
            per_category=generate_request.per_category,
            prompt_overrides=generate_request.prompt_overrides,
            docs=generate_request.source_files,
            progress_callback=qg_category_progress,
            category_complete_callback=qg_category_complete,
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
        
        # Step 3: Retrieval
        if progress_callback:
            await progress_callback("retrieval", "start", {"message": "开始检索..."})
        
        # 获取 RagFlow 配置
        ragflow_api_url = os.getenv("RAGFLOW_API_URL", "")
        ragflow_api_key = os.getenv("RAGFLOW_API_KEY", "")
        datasets_json_path = os.getenv("RAGFLOW_DATASETS_JSON", None)
        
        if not ragflow_api_url or not ragflow_api_key:
            logger.warning("RagFlow API 配置未设置，跳过检索模块")
        results["retrieval"] = {
            "status": "skipped",
                "message": "RagFlow API 配置未设置",
            }
            if progress_callback:
                await progress_callback("retrieval", "skipped", results["retrieval"])
        else:
            # 检索配置（可从环境变量读取）
            retrieval_config = {
                "top_k": int(os.getenv("RAGFLOW_TOP_K", "5")),
                "similarity_threshold": float(os.getenv("RAGFLOW_SIMILARITY_THRESHOLD", "0.0")),
                "vector_similarity_weight": float(os.getenv("RAGFLOW_VECTOR_SIMILARITY_WEIGHT", "0.3")) if os.getenv("RAGFLOW_VECTOR_SIMILARITY_WEIGHT") else None,
            }
            
            async def retrieval_progress(current: int, total: int, data: Dict):
                if progress_callback:
                    await progress_callback("retrieval", "progress", {
                        "current": current,
                        "total": total,
                        "percentage": int((current / total) * 100) if total > 0 else 0,
                        **data
                    })
            
            retrieval_result = await run_retrieval(
                csv_path=csv_path,
                ragflow_api_url=ragflow_api_url,
                ragflow_api_key=ragflow_api_key,
                retrieval_config=retrieval_config,
                datasets_json_path=datasets_json_path,
                max_workers=int(os.getenv("RAGFLOW_MAX_WORKERS", "1")),
                delay_between_requests=float(os.getenv("RAGFLOW_DELAY", "0.5")),
                progress_callback=retrieval_progress,
            )
            
            results["retrieval"] = retrieval_result
        
        if progress_callback:
                await progress_callback("retrieval", "complete", retrieval_result)
            
            # 使用检索后的 CSV 进行评测
            csv_path = retrieval_result["output_csv_path"]
        
        # Step 4: Evaluation
        if progress_callback:
            await progress_callback("evaluation", "start", {"message": "开始评测..."})
        
        # 检查是否有检索结果
        if results["retrieval"] and results["retrieval"].get("status") != "skipped":
            async def evaluation_progress(current: int, total: int, data: Dict):
                if progress_callback:
                    await progress_callback("evaluation", "progress", {
                        "current": current,
                        "total": total,
                        "percentage": int((current / total) * 100) if total > 0 else 0,
                        **data
                    })
            
            evaluation_result = await run_evaluation(
                csv_path=csv_path,
                output_dir=None,  # 使用默认目录
                progress_callback=evaluation_progress,
            )
            
            results["evaluation"] = evaluation_result
            
            if progress_callback:
                await progress_callback("evaluation", "complete", evaluation_result)
        else:
        results["evaluation"] = {
            "status": "skipped",
                "message": "检索模块未完成，跳过评测",
        }
        
        if progress_callback:
            await progress_callback("evaluation", "skipped", results["evaluation"])
        
        return results
        
    except Exception as e:
        logger.error(f"Pipeline error: {e}")
        if progress_callback:
            await progress_callback("error", "error", {"message": str(e)})
        raise

