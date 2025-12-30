"""Retrieval module routes."""
import logging
import os
import traceback
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException

from services.retrieval_service import run_retrieval, load_test_cases_from_csv
from config.paths import DATA_RETRIEVAL_DIR

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/api/retrieval/run")
async def run_retrieval_api(request: Dict):
    """Run retrieval module on a given CSV."""
    import time
    api_start_time = time.time()
    
    csv_path = request.get("csv_path")
    if not csv_path:
        raise HTTPException(status_code=400, detail="csv_path is required")
    try:
        logger.info(f"[API] 检索请求开始: {csv_path}")
        
        ragflow_api_url = os.getenv("RAGFLOW_API_URL", "")
        ragflow_api_key = os.getenv("RAGFLOW_API_KEY", "")
        datasets_json_path = os.getenv("RAGFLOW_DATASETS_JSON", None)

        if not ragflow_api_url or not ragflow_api_key:
            raise HTTPException(status_code=400, detail="RagFlow API 配置未设置")

        retrieval_config = {
            "top_k": int(os.getenv("RAGFLOW_TOP_K", "5")),
            "similarity_threshold": float(os.getenv("RAGFLOW_SIMILARITY_THRESHOLD", "0.0")),
            "vector_similarity_weight": (
                float(os.getenv("RAGFLOW_VECTOR_SIMILARITY_WEIGHT", "0.3"))
                if os.getenv("RAGFLOW_VECTOR_SIMILARITY_WEIGHT")
                else None
            ),
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
        
        api_total_time = time.time() - api_start_time
        logger.info(f"[API] 检索请求完成: 总耗时 {api_total_time:.2f} 秒")
        result["api_total_time"] = api_total_time

        return result
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        error_msg = str(exc)
        error_trace = traceback.format_exc()
        logger.error("Retrieval error: %s\n%s", error_msg, error_trace)
        raise HTTPException(status_code=500, detail=error_msg)


@router.get("/api/retrieval/results")
async def list_retrieval_results():
    """List all retrieval result CSV files."""
    try:
        result_files = []
        if DATA_RETRIEVAL_DIR.exists():
            for csv_file in DATA_RETRIEVAL_DIR.glob("*_with_answers.csv"):
                stat = csv_file.stat()
                result_files.append({
                    "path": str(csv_file),
                    "filename": csv_file.name,
                    "size": stat.st_size,
                    "modified_at": stat.st_mtime,
                })
        # 按修改时间倒序排列
        result_files.sort(key=lambda x: x["modified_at"], reverse=True)
        return {"result_files": result_files}
    except Exception as exc:
        logger.error(f"List retrieval results error: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/api/retrieval/result")
async def get_retrieval_result(csv_path: str) -> Dict[str, Any]:
    """Return detailed retrieval results for a given CSV（用于前端分页展示）."""
    try:
        csv_path_obj = Path(csv_path)

        # 仅允许访问检索结果目录下的文件，防止任意文件读取
        base_dir = DATA_RETRIEVAL_DIR.resolve()
        if csv_path_obj.is_absolute():
            full_path = csv_path_obj
        else:
            # 支持传入完整相对路径或仅文件名
            if csv_path_obj.parent == Path(""):
                full_path = base_dir / csv_path_obj.name
            else:
                full_path = Path(csv_path).resolve()

        full_path = full_path.resolve()
        if base_dir not in full_path.parents and base_dir != full_path.parent:
            raise HTTPException(status_code=400, detail="无效的 CSV 路径")

        if not full_path.exists():
            raise HTTPException(status_code=404, detail="CSV 文件不存在")

        test_cases = load_test_cases_from_csv(str(full_path))
        items: List[Dict[str, str]] = []
        for tc in test_cases:
            items.append(
                {
                    "question": tc.question,
                    "answer": tc.answer,
                    "reference": tc.reference,
                    "type": tc.type or "",
                    "theme": tc.theme or "",
                }
            )
        return {"items": items, "total": len(items), "csv_path": str(full_path)}
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        error_msg = str(exc)
        error_trace = traceback.format_exc()
        logger.error("Get retrieval result error: %s\n%s", error_msg, error_trace)
        raise HTTPException(status_code=500, detail=error_msg)


