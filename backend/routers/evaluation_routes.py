"""Evaluation module routes."""
import logging
import traceback
import json
from pathlib import Path
from typing import Dict, Optional, Literal, List
import csv

from fastapi import APIRouter, HTTPException

from services.evaluation_service import run_evaluation, EvaluationMode
from config.paths import DATA_EVALUATION_DIR

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/api/evaluation/run")
async def run_evaluation_api(request: Dict):
    """
    Run evaluation module on a given CSV with answers.
    
    Request body:
    {
        "csv_path": str,  # Required: path to CSV file with answers
        "output_dir": str,  # Optional: output directory
        "mode": str  # Optional: evaluation mode ("chapter_match", "ragas", "hybrid"), default: "hybrid"
    }
    """
    csv_path = request.get("csv_path")
    if not csv_path:
        raise HTTPException(status_code=400, detail="csv_path is required")
    
    output_dir = request.get("output_dir")
    mode_str = request.get("mode", "hybrid")
    
    # 验证模式
    valid_modes = ["chapter_match", "ragas", "hybrid"]
    if mode_str not in valid_modes:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid mode: {mode_str}. Must be one of {valid_modes}"
        )
    
    mode: EvaluationMode = mode_str  # type: ignore
    
    try:
        result = await run_evaluation(
            csv_path=csv_path,
            output_dir=output_dir,
            mode=mode
        )
        return result
    except Exception as exc:  # noqa: BLE001
        error_msg = str(exc)
        error_trace = traceback.format_exc()
        logger.error("Evaluation error: %s\n%s", error_msg, error_trace)
        raise HTTPException(status_code=500, detail=error_msg)


@router.get("/api/evaluation/results")
async def list_evaluation_results():
    """List all evaluation result summary files."""
    try:
        result_files: List[Dict] = []
        if DATA_EVALUATION_DIR.exists():
            # 查找所有 evaluation_summary.json 文件
            for summary_file in DATA_EVALUATION_DIR.glob("**/evaluation_summary.json"):
                try:
                    stat = summary_file.stat()
                    # 读取摘要以获取基本信息
                    with open(summary_file, "r", encoding="utf-8") as f:
                        summary_data = json.load(f)
                    
                    # 查找对应的 CSV 文件
                    csv_file = summary_file.parent / "evaluation_results.csv"
                    csv_size = csv_file.stat().st_size if csv_file.exists() else 0
                    
                    # 返回相对于 DATA_EVALUATION_DIR 的路径，或者完整路径
                    # 使用相对路径更清晰，但需要确保前端能正确处理
                    summary_path_rel = summary_file.relative_to(DATA_EVALUATION_DIR) if summary_file.is_relative_to(DATA_EVALUATION_DIR) else str(summary_file)
                    csv_path_rel = csv_file.relative_to(DATA_EVALUATION_DIR) if csv_file.exists() and csv_file.is_relative_to(DATA_EVALUATION_DIR) else (str(csv_file) if csv_file.exists() else None)
                    
                    result_files.append({
                        "summary_path": str(summary_file),  # 保持完整路径，前端会正确处理
                        "csv_path": str(csv_file) if csv_file.exists() else None,
                        "filename": summary_file.parent.name,  # 目录名作为文件名
                        "size": csv_size,
                        "modified_at": stat.st_mtime,
                        "mode": summary_data.get("mode", "hybrid"),
                        "total_questions": summary_data.get("total_questions", 0),
                    })
                except Exception as e:
                    logger.warning(f"Error reading evaluation summary {summary_file}: {e}")
                    continue
        
        # 按修改时间倒序排列
        result_files.sort(key=lambda x: x["modified_at"], reverse=True)
        return {"result_files": result_files}
    except Exception as exc:
        logger.error(f"List evaluation results error: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/api/evaluation/result")
async def get_evaluation_result(summary_path: str) -> Dict:
    """Get evaluation result by summary JSON path,并返回题目级 KPI。"""
    try:
        summary_path_obj = Path(summary_path)
        base_dir = DATA_EVALUATION_DIR.resolve()
        
        # 处理路径：如果路径是相对路径且包含 data/evaluation/ 前缀，需要去掉前缀
        if summary_path_obj.is_absolute():
            full_path = summary_path_obj
        else:
            # 如果路径以 data/evaluation/ 开头，去掉这个前缀
            path_str = str(summary_path_obj)
            if path_str.startswith("data/evaluation/"):
                # 去掉 data/evaluation/ 前缀
                relative_path = Path(path_str.replace("data/evaluation/", "", 1))
                full_path = base_dir / relative_path
            elif path_str.startswith("evaluation/"):
                # 去掉 evaluation/ 前缀
                relative_path = Path(path_str.replace("evaluation/", "", 1))
                full_path = base_dir / relative_path
            else:
                # 直接使用相对路径
                full_path = base_dir / summary_path_obj
        
        full_path = full_path.resolve()
        
        # 安全检查：确保路径在 base_dir 下
        try:
            full_path.relative_to(base_dir)
        except ValueError:
            raise HTTPException(status_code=400, detail="无效的摘要文件路径：路径不在评测结果目录下")
        
        if not full_path.exists():
            raise HTTPException(status_code=404, detail=f"摘要文件不存在: {full_path}")
        
        # 读取摘要
        with open(full_path, "r", encoding="utf-8") as f:
            summary = json.load(f)
        
        # 查找对应的 CSV 文件
        csv_file = full_path.parent / "evaluation_results.csv"
        results_csv_path = str(csv_file) if csv_file.exists() else None

        items: List[Dict] = []
        if csv_file.exists():
            try:
                with open(csv_file, "r", encoding="utf-8") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        # 只保留主要字段，避免返回过大的上下文字段
                        item = {
                            "question": row.get("question", ""),
                            "answer": row.get("answer", ""),
                            "reference": row.get("reference", ""),
                            "type": row.get("type", ""),
                            "theme": row.get("theme", ""),
                            "retrieved_context": row.get("retrieved_context", ""),
                        }
                        # 可选指标
                        for key in [
                            "chapter_match_accuracy",
                            "chapter_match_recall",
                            "chapter_matched",
                            "hybrid_score",
                            "hybrid_matched",
                            "ragas_overall_score",
                            "ragas_quality_score",
                            "ragas_relevancy_score",
                            "ragas_faithfulness_score",
                        ]:
                            if key in row:
                                try:
                                    item[key] = float(row[key]) if row[key] not in ("", None) else None
                                except ValueError:
                                    item[key] = row[key]
                        items.append(item)
            except Exception as e:
                logger.warning(f"读取评测结果明细失败: {e}")
        
        return {
            "summary": summary,
            "results_csv_path": results_csv_path,
            "summary_json_path": str(full_path),
            "total_questions": summary.get("total_questions", 0),
            "total_time": summary.get("total_time", 0),
            "mode": summary.get("mode", "hybrid"),
            "items": items,
        }
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        error_msg = str(exc)
        error_trace = traceback.format_exc()
        logger.error("Get evaluation result error: %s\n%s", error_msg, error_trace)
        raise HTTPException(status_code=500, detail=error_msg)


