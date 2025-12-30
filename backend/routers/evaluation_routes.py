"""Evaluation module routes."""
import logging
import traceback
import json
import time
from pathlib import Path
from typing import Dict, List
import csv

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from starlette.websockets import WebSocketState
from websockets.exceptions import ConnectionClosedError

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
    api_start_time = time.time()
    
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
        logger.info(f"[API] 评测请求开始: {csv_path}, 模式: {mode}")
        
        # 读取Ragas指标配置
        ragas_metrics_config = request.get("ragas_metrics_config")
        
        result = await run_evaluation(
            csv_path=csv_path,
            output_dir=output_dir,
            mode=mode,
            ragas_metrics_config=ragas_metrics_config
        )
        
        api_total_time = time.time() - api_start_time
        logger.info(f"[API] 评测请求完成: 总耗时 {api_total_time:.2f} 秒")
        result["api_total_time"] = api_total_time
        
        return result
    except ValueError as ve:
        # 用户输入错误，返回400
        error_msg = str(ve)
        logger.warning(f"评测请求参数错误: {error_msg}")
        raise HTTPException(status_code=400, detail=error_msg)
    except Exception as exc:  # noqa: BLE001
        error_msg = str(exc)
        error_type = type(exc).__name__
        error_trace = traceback.format_exc()
        logger.error(f"Evaluation error ({error_type}): {error_msg}\n{error_trace}")
        # 返回友好的错误消息，避免暴露内部细节
        friendly_msg = f"评测过程中发生错误: {error_type}"
        if len(error_msg) < 200:  # 如果错误消息不长，可以包含
            friendly_msg = f"{friendly_msg} - {error_msg}"
        raise HTTPException(status_code=500, detail=friendly_msg)


@router.websocket("/ws/evaluation/progress")
async def websocket_evaluation(websocket: WebSocket):
    """WebSocket endpoint for running evaluation with real-time progress updates."""
    await websocket.accept()
    try:
        data = await websocket.receive_text()
        request_data = json.loads(data)
        
        csv_path = request_data.get("csv_path")
        if not csv_path:
            await websocket.send_json({"type": "error", "message": "csv_path is required"})
            return
        
        output_dir = request_data.get("output_dir")
        mode_str = request_data.get("mode", "hybrid")
        ragas_metrics_config = request_data.get("ragas_metrics_config")
        
        # 验证模式
        valid_modes = ["chapter_match", "ragas", "hybrid"]
        if mode_str not in valid_modes:
            await websocket.send_json({
                "type": "error",
                "message": f"Invalid mode: {mode_str}. Must be one of {valid_modes}"
            })
            return
        
        mode: EvaluationMode = mode_str  # type: ignore
        
        # 定义进度回调函数
        evaluation_start_time = time.time()
        
        async def progress_callback(current: int, total: int, data: Dict):
            """Send progress updates via WebSocket."""
            try:
                # 检查WebSocket连接状态
                if websocket.client_state != WebSocketState.CONNECTED:
                    return  # 连接已断开，静默返回
                
                # 优先使用 data 中的 current（更准确），否则使用函数参数
                actual_current = data.get("current", current)
                
                elapsed_time = time.time() - evaluation_start_time
                avg_time_per_item = elapsed_time / actual_current if actual_current > 0 else 0
                remaining = total - actual_current
                estimated_remaining = avg_time_per_item * remaining if remaining > 0 else 0
                
                await websocket.send_json({
                    "type": "progress",
                    "current": actual_current,
                    "total": total,
                    "percentage": int((actual_current / total) * 100) if total > 0 else 0,
                    "elapsed_time": elapsed_time,
                    "estimated_remaining": estimated_remaining,
                    "status": data.get("status", "evaluating"),
                    "mode": data.get("mode", mode),
                })
            except (WebSocketDisconnect, RuntimeError) as e:
                # WebSocket连接已断开，记录警告但不中断评测
                logger.warning(f"WebSocket连接已断开，无法发送进度更新: {e}")
                return  # 静默失败
            except Exception as e:
                # 其他异常也静默处理，避免中断评测
                logger.warning(f"发送进度更新失败: {e}")
                return
        
        logger.info(f"[WebSocket] 评测请求开始: {csv_path}, 模式: {mode}")
        
        # 发送开始消息
        await websocket.send_json({
            "type": "start",
            "message": "开始评测...",
            "mode": mode
        })
        
        # 创建一个包装的进度回调，在第一次调用时发送初始进度
        initial_progress_sent = False
        
        async def wrapped_progress_callback(current: int, total: int, data: Dict):
            nonlocal initial_progress_sent
            # 第一次调用时，如果total > 0，发送一个初始进度更新
            if not initial_progress_sent and total > 0:
                initial_progress_sent = True
                try:
                    if websocket.client_state == WebSocketState.CONNECTED:
                        elapsed = time.time() - evaluation_start_time
                        await websocket.send_json({
                            "type": "progress",
                            "current": 0,
                            "total": total,
                            "percentage": 0,
                            "elapsed_time": elapsed,
                            "estimated_remaining": 0,
                            "status": "initializing",
                            "mode": mode,
                        })
                except Exception:
                    pass  # 静默失败
            # 调用原始回调（传递实际完成数）
            await progress_callback(current, total, data)
        
        # 运行评测
        result = await run_evaluation(
            csv_path=csv_path,
            output_dir=output_dir,
            mode=mode,
            progress_callback=wrapped_progress_callback,
            ragas_metrics_config=ragas_metrics_config
        )
        
        # 发送完成消息
        await websocket.send_json({
            "type": "complete",
            "results": result
        })
        
    except WebSocketDisconnect:
        logger.info("WebSocket disconnected during evaluation")
    except Exception as exc:  # noqa: BLE001
        error_msg = str(exc)
        error_type = type(exc).__name__
        error_trace = traceback.format_exc()
        logger.error(f"Evaluation WebSocket error ({error_type}): {error_msg}\n{error_trace}")
        try:
            friendly_msg = f"评测过程中发生错误: {error_type}"
            if len(error_msg) < 200:
                friendly_msg = f"{friendly_msg} - {error_msg}"
            await websocket.send_json({"type": "error", "message": friendly_msg})
        except Exception:  # noqa: BLE001
            pass


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
                # 使用 utf-8-sig 编码自动处理 BOM 字符
                with open(csv_file, "r", encoding="utf-8-sig") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        # 处理可能的 BOM 字符：同时检查 "question" 和 "\ufeffquestion"
                        question_key = "question"
                        if question_key not in row:
                            # 尝试查找带 BOM 的键
                            for key in row.keys():
                                if key.strip().lower() == "question" or key.replace("\ufeff", "") == "question":
                                    question_key = key
                                    break
                        
                        # 只保留主要字段，避免返回过大的上下文字段
                        item = {
                            "question": row.get(question_key, row.get("question", "")).strip(),
                            "answer": row.get("answer", "").strip(),
                            "reference": row.get("reference", "").strip(),
                            "type": row.get("type", "").strip(),
                            "theme": row.get("theme", "").strip(),
                            "retrieved_context": row.get("retrieved_context", "").strip(),
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
                            "recall_at_3",
                            "recall_at_5",
                            "recall_at_10",
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


@router.get("/api/evaluation/download/csv")
async def download_evaluation_csv(csv_path: str):
    """Download evaluation results CSV file."""
    try:
        csv_path_obj = Path(csv_path)
        base_dir = DATA_EVALUATION_DIR.resolve()
        
        # 处理路径
        if csv_path_obj.is_absolute():
            full_path = csv_path_obj
        else:
            # 如果路径以 data/evaluation/ 开头，去掉这个前缀
            path_str = str(csv_path_obj)
            if path_str.startswith("data/evaluation/"):
                relative_path = Path(path_str.replace("data/evaluation/", "", 1))
                full_path = base_dir / relative_path
            elif path_str.startswith("evaluation/"):
                relative_path = Path(path_str.replace("evaluation/", "", 1))
                full_path = base_dir / relative_path
            else:
                full_path = base_dir / csv_path_obj
        
        full_path = full_path.resolve()
        
        # 安全检查
        try:
            full_path.relative_to(base_dir)
        except ValueError:
            raise HTTPException(status_code=400, detail="无效的 CSV 文件路径")
        
        if not full_path.exists():
            raise HTTPException(status_code=404, detail="CSV 文件不存在")
        
        return FileResponse(
            str(full_path),
            media_type="text/csv",
            filename=full_path.name,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Download CSV error: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/api/evaluation/download/json")
async def download_evaluation_json(json_path: str):
    """Download evaluation summary JSON file."""
    try:
        json_path_obj = Path(json_path)
        base_dir = DATA_EVALUATION_DIR.resolve()
        
        # 处理路径
        if json_path_obj.is_absolute():
            full_path = json_path_obj
        else:
            # 如果路径以 data/evaluation/ 开头，去掉这个前缀
            path_str = str(json_path_obj)
            if path_str.startswith("data/evaluation/"):
                relative_path = Path(path_str.replace("data/evaluation/", "", 1))
                full_path = base_dir / relative_path
            elif path_str.startswith("evaluation/"):
                relative_path = Path(path_str.replace("evaluation/", "", 1))
                full_path = base_dir / relative_path
            else:
                full_path = base_dir / json_path_obj
        
        full_path = full_path.resolve()
        
        # 安全检查
        try:
            full_path.relative_to(base_dir)
        except ValueError:
            raise HTTPException(status_code=400, detail="无效的 JSON 文件路径")
        
        if not full_path.exists():
            raise HTTPException(status_code=404, detail="JSON 文件不存在")
        
        return FileResponse(
            str(full_path),
            media_type="application/json",
            filename=full_path.name,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Download JSON error: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


