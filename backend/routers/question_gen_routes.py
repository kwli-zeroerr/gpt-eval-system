"""Question generation and related WebSocket routes."""
import json
import logging
import traceback
from typing import Dict, List

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from schemas import CategoriesResponse, GenerateRequest, GenerateResponse, QuestionItem
from services.generator import generate_questions
from services.templates import DEFAULT_CATEGORIES
from services.question_logger import save_questions_to_log
from services.minio_client import MinIOClient

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/categories", response_model=CategoriesResponse)
async def list_categories():
    """List all question categories (S1-S6) in a stable order."""
    sorted_categories = sorted(DEFAULT_CATEGORIES, key=lambda c: c.id)
    return CategoriesResponse(categories=sorted_categories)


@router.get("/api/source-documents")
async def list_source_documents():
    """List all source documents available in MinIO knowledge bucket with statistics."""
    try:
        minio_client = MinIOClient()
        files = minio_client.list_files(max_items=1000)
        
        # Extract unique dataset names from file paths
        # Format: {dataset_id}/ocr_result/{subdir}/{filename}
        datasets = {}
        file_types = {}  # 统计文件类型分布
        
        for file_path in files:
            # 统计文件类型
            if "." in file_path:
                file_ext = file_path.split(".")[-1].lower()
                file_types[file_ext] = file_types.get(file_ext, 0) + 1
            
            parts = file_path.split("/")
            if len(parts) >= 2:
                dataset_id = parts[0]
                if dataset_id not in datasets:
                    datasets[dataset_id] = {
                        "dataset_id": dataset_id,
                        "file_count": 0,
                        "files": []
                    }
                datasets[dataset_id]["file_count"] += 1
                datasets[dataset_id]["files"].append(file_path)
        
        # 计算统计信息
        total_size = 0  # 如果需要，可以从MinIO获取文件大小
        avg_files_per_dataset = len(files) / len(datasets) if datasets else 0
        
        return {
            "total_files": len(files),
            "total_datasets": len(datasets),
            "avg_files_per_dataset": round(avg_files_per_dataset, 2),
            "file_type_distribution": file_types,
            "datasets": list(datasets.values()),
            "all_files": files[:100],  # Limit to first 100 for preview
            "statistics": {
                "total_files": len(files),
                "total_datasets": len(datasets),
                "file_types": len(file_types),
                "most_common_type": max(file_types.items(), key=lambda x: x[1])[0] if file_types else None
            }
        }
    except Exception as exc:  # noqa: BLE001
        error_msg = str(exc)
        logger.error("Error listing source documents: %s", error_msg)
        raise HTTPException(status_code=500, detail=error_msg)


@router.get("/api/question-analysis")
async def analyze_questions():
    """Analyze generated questions for generalization level."""
    try:
        from services.question_logger import get_latest_log_path, load_questions_from_log
        
        # 获取最新的问题日志
        latest_log = get_latest_log_path()
        if not latest_log or not latest_log.exists():
            return {
                "error": "No question log found",
                "generalization_level": "unknown"
            }
        
        questions, _, _ = load_questions_from_log(str(latest_log))
        
        if not questions:
            return {
                "error": "No questions in log",
                "generalization_level": "unknown"
            }
        
        # 分析问题泛化性
        # 具体问题特征：包含具体数值、具体名称、具体章节引用
        # 泛化问题特征：抽象概念、通用流程、跨文档问题
        
        specific_indicators = [
            "是多少", "是多少V", "是多少A", "是多少Hz",  # 数值问题
            "0x", "错误码", "报警",  # 错误码
            "章节", "第", "页",  # 章节引用
        ]
        
        generalization_indicators = [
            "如何", "怎样", "什么方法",  # 方法类
            "为什么", "原因",  # 原因类
            "什么", "哪些", "包括",  # 概念类
            "配置", "设置", "流程",  # 流程类
        ]
        
        specific_count = 0
        generalization_count = 0
        mixed_count = 0
        
        for q in questions:
            text = q.text.lower()
            has_specific = any(indicator in text for indicator in specific_indicators)
            has_generalization = any(indicator in text for indicator in generalization_indicators)
            
            if has_specific and has_generalization:
                mixed_count += 1
            elif has_specific:
                specific_count += 1
            elif has_generalization:
                generalization_count += 1
            else:
                # 无法明确分类，归为混合
                mixed_count += 1
        
        total = len(questions)
        specific_ratio = specific_count / total if total > 0 else 0
        generalization_ratio = generalization_count / total if total > 0 else 0
        mixed_ratio = mixed_count / total if total > 0 else 0
        
        # 确定泛化级别
        if generalization_ratio > 0.5:
            generalization_level = "high"  # 高泛化
        elif mixed_ratio > 0.4:
            generalization_level = "medium"  # 中等泛化
        elif specific_ratio > 0.6:
            generalization_level = "low"  # 低泛化（具体问题为主）
        else:
            generalization_level = "balanced"  # 平衡
        
        return {
            "total_questions": total,
            "specific_questions": specific_count,
            "generalization_questions": generalization_count,
            "mixed_questions": mixed_count,
            "ratios": {
                "specific": round(specific_ratio * 100, 2),
                "generalization": round(generalization_ratio * 100, 2),
                "mixed": round(mixed_ratio * 100, 2)
            },
            "generalization_level": generalization_level,
            "latest_log_path": str(latest_log)
        }
    except Exception as exc:  # noqa: BLE001
        error_msg = str(exc)
        logger.error("Error analyzing questions: %s", error_msg)
        raise HTTPException(status_code=500, detail=error_msg)


@router.post("/api/generate", response_model=GenerateResponse)
async def generate(req: GenerateRequest):
    """HTTP endpoint for one-shot question generation (no streaming progress)."""
    try:
        logger.info(
            "Generating questions for categories: %s, per_category: %s",
            req.categories,
            req.per_category,
        )
        req_id, qs, category_times, total_time = await generate_questions(
            categories=req.categories,
            per_category=req.per_category,
            prompt_overrides=req.prompt_overrides,
            docs=req.source_files,
        )
        logger.info(
            "Generated %d questions in %.2fs, request_id: %s",
            len(qs),
            total_time,
            req_id,
        )

        # Save to log file with timing information
        try:
            log_path = save_questions_to_log(req_id, qs, category_times, total_time)
            logger.info("Questions saved to log: %s", log_path)
        except Exception as log_err:  # noqa: BLE001
            logger.warning("Failed to save log: %s", log_err)

        return GenerateResponse(request_id=req_id, questions=qs)
    except Exception as exc:  # noqa: BLE001
        error_msg = str(exc)
        error_trace = traceback.format_exc()
        logger.error("Error generating questions: %s\n%s", error_msg, error_trace)
        raise HTTPException(status_code=500, detail=error_msg)


@router.websocket("/ws/progress")
async def websocket_progress(websocket: WebSocket):
    """Legacy WebSocket endpoint for simple connectivity check / progress echo."""
    await websocket.accept()
    try:
        while True:
            await websocket.receive_text()
            await websocket.send_json({"status": "connected"})
    except WebSocketDisconnect:
        # Client disconnected, nothing special to do
        pass


async def generate_with_progress(req: GenerateRequest, websocket: WebSocket):
    """Generate questions with fine-grained progress updates via WebSocket."""
    category_questions_map: Dict[str, List[QuestionItem]] = {}

    async def progress_callback(cat: str, current: int, total: int, elapsed: float | None = None, active_categories: List[str] | None = None):
        # Handle both old signature (without active_categories) and new signature
        if active_categories is None:
            active_categories = [cat] if cat else []
        await websocket.send_json(
            {
                "type": "progress",
                "category": cat,  # Keep for backward compatibility
                "activeCategories": active_categories,  # Support multiple concurrent categories
                "current": current,
                "total": total,
                "percentage": int((current / total) * 100),
                "elapsed": elapsed,
            }
        )

    async def category_complete_callback(
        cat: str, questions: List[QuestionItem], elapsed: float
    ):
        """Send questions for a category as soon as it's completed."""
        category_questions_map[cat] = questions
        await websocket.send_json(
            {
                "type": "category_complete",
                "category": cat,
                "questions": [
                    {
                        "category": q.category,
                        "text": q.text,
                        "reference": q.reference or "",
                    }
                    for q in questions
                ],
                "elapsed": elapsed,
            }
        )

    try:
        total_categories = len(req.categories)
        total_questions = total_categories * req.per_category

        await websocket.send_json(
            {
                "type": "start",
                "total": total_questions,
                "categories": req.categories,
            }
        )

        req_id, qs, category_times, total_time = await generate_questions(
            categories=req.categories,
            per_category=req.per_category,
            prompt_overrides=req.prompt_overrides,
            docs=req.source_files,
            progress_callback=progress_callback,
            category_complete_callback=category_complete_callback,
        )

        # Save to log with timing information
        try:
            log_path = save_questions_to_log(req_id, qs, category_times, total_time)
            await websocket.send_json({"type": "log_saved", "path": log_path})
        except Exception as log_err:  # noqa: BLE001
            logger.warning("Failed to save log: %s", log_err)

        await websocket.send_json(
            {
                "type": "complete",
                "request_id": req_id,
                "questions": [
                    {
                        "category": q.category,
                        "text": q.text,
                        "reference": q.reference,
                    }
                    for q in qs
                ],
                "total": len(qs),
                "category_times": category_times,
                "total_time": total_time,
            }
        )
    except Exception as exc:  # noqa: BLE001
        await websocket.send_json({"type": "error", "message": str(exc)})


@router.websocket("/ws/generate")
async def websocket_generate(websocket: WebSocket):
    """WebSocket endpoint for generating questions with real-time progress."""
    logger.info("WebSocket connection attempt to /ws/generate")
    await websocket.accept()
    logger.info("WebSocket connection accepted")
    try:
        data = await websocket.receive_text()
        logger.info("Received WebSocket data: %s", data[:200] if len(data) > 200 else data)
        req_data = json.loads(data)
        req = GenerateRequest(**req_data)
        logger.info(
            "Starting question generation: categories=%s, per_category=%s",
            req.categories,
            req.per_category,
        )
        await generate_with_progress(req, websocket)
        logger.info("Question generation completed successfully")
    except WebSocketDisconnect:
        # Client disconnected during generation
        logger.info("WebSocket disconnected by client")
    except Exception as exc:  # noqa: BLE001
        error_msg = str(exc)
        error_trace = traceback.format_exc()
        logger.error("WebSocket error: %s\n%s", error_msg, error_trace)
        try:
            await websocket.send_json({"type": "error", "message": error_msg})
        except Exception:  # noqa: BLE001
            # If sending fails there's nothing more we can do
            logger.error("Failed to send error message to WebSocket client")
            pass


