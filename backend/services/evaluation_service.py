"""
评测服务 - 支持章节匹配和 Ragas AI 评测的混合评测系统
"""
import csv
import json
import logging
import os
import pandas as pd
import numpy as np
from pathlib import Path
from typing import List, Dict, Optional, Callable, Awaitable, Literal
from dataclasses import dataclass
import asyncio
import time
import sys
from config.paths import DATA_EVALUATION_DIR

from services.chapter_matcher import ChapterMatcher
from services.ragas_evaluator import RagasEvaluator

# 增加 CSV 字段大小限制（默认 131072 字节，增加到 10MB）
csv.field_size_limit(min(sys.maxsize, 10 * 1024 * 1024))

logger = logging.getLogger(__name__)

# 评测模式类型
EvaluationMode = Literal["chapter_match", "ragas", "hybrid"]


@dataclass
class TestCase:
    """测试用例结构"""
    question: str
    answer: str  # 完整答案
    answer_chapter: str  # 从答案中提取的章节信息
    reference: str  # 标注的章节信息
    type: Optional[str] = None
    theme: Optional[str] = None
    retrieved_context: str = ""  # 检索到的上下文（用于Ragas评测）
    retrieved_chunks_json: str = ""  # 检索到的完整chunks列表（JSON格式，用于召回率@K计算）
    retrieval_time: float = 0.0  # 检索响应时间（秒）
    generation_time: float = 0.0  # 生成响应时间（秒）
    total_time: float = 0.0  # 总响应时间（秒）


class MetricsCalculator:
    """指标计算器 - 基于章节匹配逻辑"""
    
    @staticmethod
    def calculate_accuracy(retrieved_chapters: List[str], reference_chapter: str) -> Dict[str, float]:
        """计算准确率和召回率"""
        if not reference_chapter:
            return {'correct_count': 0, 'total_count': 0, 'accuracy': 0.0, 'recall': 0.0}
        
        correct_count = 0
        total_retrieved = len(retrieved_chapters)
        
        for retrieved_chapter in retrieved_chapters:
            if ChapterMatcher.is_valid_match(retrieved_chapter, reference_chapter):
                correct_count += 1
        
        accuracy = correct_count / total_retrieved if total_retrieved > 0 else 0.0
        recall = 1.0 if correct_count > 0 else 0.0
        
        return {
            'correct_count': correct_count,
            'total_count': total_retrieved,
            'accuracy': accuracy,
            'recall': recall
        }
    
    @staticmethod
    def recall_at_k(retrieved_chapters: List[str], reference_chapter: str, k: int) -> float:
        """计算Recall@K"""
        if not reference_chapter:
            return 0.0
        retrieved_k = retrieved_chapters[:k]
        for chapter in retrieved_k:
            if ChapterMatcher.is_valid_match(chapter, reference_chapter):
                return 1.0
        return 0.0


def extract_chapter_from_chunk(chunk: Dict) -> Optional[str]:
    """
    从chunk中提取章节信息
    
    优先从metadata提取，回退到content提取
    
    Args:
        chunk: 检索返回的chunk字典
    
    Returns:
        章节信息字符串，如果未找到则返回None
    """
    if not isinstance(chunk, dict):
        return None
    
    # 方法1：从metadata中提取
    metadata = chunk.get('metadata', {})
    if isinstance(metadata, dict):
        # 尝试从document_name或其他metadata字段提取
        document_name = metadata.get('document_name', '')
        if document_name:
            chapter_info = ChapterMatcher.extract_chapter_info(document_name)
            if chapter_info:
                return chapter_info
        
        # 尝试从其他metadata字段提取
        for key, value in metadata.items():
            if isinstance(value, str) and value:
                chapter_info = ChapterMatcher.extract_chapter_info(value)
                if chapter_info:
                    return chapter_info
    
    # 方法2：从content中提取
    content = chunk.get('content', '')
    if content:
        chapter_info = ChapterMatcher.extract_chapter_info(content)
        if chapter_info:
            return chapter_info
    
    # 方法3：从important_keywords中提取
    important_keywords = chunk.get('important_keywords', [])
    if isinstance(important_keywords, list):
        for keyword in important_keywords:
            if isinstance(keyword, str) and keyword:
                chapter_info = ChapterMatcher.extract_chapter_info(keyword)
                if chapter_info:
                    return chapter_info
    
    return None


def calculate_generalization_score(results_by_type: Dict[str, List[float]]) -> float:
    """
    计算泛化性得分
    
    泛化性 = 1 - (各类型问题得分的标准差 / 平均得分)
    得分越高，说明系统对不同类型问题的处理能力越均衡
    
    Args:
        results_by_type: 按问题类型分组的得分列表，格式：{"S1": [0.8, 0.9, ...], "S2": [0.7, 0.8, ...], ...}
    
    Returns:
        泛化性得分（0-1之间）
    """
    if not results_by_type:
        return 0.0
    
    # 计算各类型问题的平均得分
    type_scores = {}
    for q_type, scores in results_by_type.items():
        if scores and len(scores) > 0:
            type_scores[q_type] = np.mean(scores)
    
    if not type_scores or len(type_scores) < 2:
        # 如果只有一种类型或没有数据，无法计算泛化性
        return 0.0
    
    # 计算所有类型得分的平均值和标准差
    all_scores = list(type_scores.values())
    avg_score = np.mean(all_scores)
    std_score = np.std(all_scores)
    
    if avg_score == 0:
        return 0.0
    
    # 泛化性 = 1 - (标准差 / 平均分)，归一化到0-1
    # 使用变异系数（CV = std/mean）来衡量离散程度
    cv = std_score / avg_score if avg_score > 0 else float('inf')
    # 将CV映射到0-1：CV越小，泛化性越高
    # 使用指数衰减：generalization = exp(-cv)，当cv=0时得1，cv增大时得分降低
    generalization = np.exp(-min(cv, 2.0))  # 限制cv最大为2.0，避免得分过低
    
    return float(max(0.0, min(1.0, generalization)))


def calculate_recall_at_k_from_chunks(retrieved_chunks_json: str, reference_chapter: str, k: int) -> float:
    """
    从检索到的chunks JSON中计算Recall@K
    
    Args:
        retrieved_chunks_json: JSON格式的chunks列表字符串
        reference_chapter: 参考章节信息
        k: 前K个chunks
    
    Returns:
        Recall@K得分（0.0或1.0）
    """
    if not retrieved_chunks_json or not reference_chapter:
        return 0.0
    
    try:
        chunks = json.loads(retrieved_chunks_json)
        if not isinstance(chunks, list) or len(chunks) == 0:
            return 0.0
        
        # 提取前K个chunks的章节信息
        top_k_chunks = chunks[:k]
        for chunk in top_k_chunks:
            chunk_chapter = extract_chapter_from_chunk(chunk)
            if chunk_chapter and ChapterMatcher.is_valid_match(chunk_chapter, reference_chapter):
                return 1.0
        
        return 0.0
    except (json.JSONDecodeError, TypeError) as e:
        logger.warning(f"解析retrieved_chunks_json失败: {e}")
        return 0.0


def load_test_cases_from_csv(csv_path: str) -> List[TestCase]:
    """从 CSV 文件加载测试用例（用于评测）"""
    test_cases = []
    
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # 兼容旧格式（没有 answer_chapter 或 retrieved_context 字段）
            answer = row.get("answer", "").strip()
            answer_chapter = row.get("answer_chapter", "").strip()
            retrieved_context = row.get("retrieved_context", "").strip()
            retrieved_chunks_json = row.get("retrieved_chunks_json", "").strip()
            
            # 解析性能指标（兼容旧格式）
            retrieval_time = float(row.get("retrieval_time", "0") or "0")
            generation_time = float(row.get("generation_time", "0") or "0")
            total_time = float(row.get("total_time", "0") or "0")
            
            # 如果 answer_chapter 为空但 answer 不为空，尝试从 answer 提取章节
            if not answer_chapter and answer:
                answer_chapter = ChapterMatcher.extract_chapter_info(answer) or ""
            
            test_cases.append(TestCase(
                question=row.get("question", "").strip(),
                answer=answer,  # 完整答案
                answer_chapter=answer_chapter,  # 章节信息
                reference=row.get("reference", "").strip(),  # 标注的章节
                type=row.get("type", "").strip() or None,
                theme=row.get("theme", "").strip() or None,
                retrieved_context=retrieved_context,  # 检索上下文
                retrieved_chunks_json=retrieved_chunks_json,  # 完整chunks列表
                retrieval_time=retrieval_time,
                generation_time=generation_time,
                total_time=total_time,
            ))
    
    logger.info(f"从 CSV 加载测试用例用于评测: {len(test_cases)} 条")
    return test_cases


def evaluate_chapter_match(test_case: TestCase) -> Dict:
    """
    使用章节匹配方法评测单个测试用例
    
    比较检索得到的章节（answer_chapter）和标注的章节（reference）
    """
    # 提取章节信息
    answer_chapter = test_case.answer_chapter or (ChapterMatcher.extract_chapter_info(test_case.answer) if test_case.answer else None)
    reference_chapter = ChapterMatcher.extract_chapter_info(test_case.reference) if test_case.reference else None
    
    # 计算准确率（二元：是否匹配）
    accuracy = 1.0 if (answer_chapter and reference_chapter and 
                      ChapterMatcher.is_valid_match(answer_chapter, reference_chapter)) else 0.0
    
    # 召回率（这里简化为与准确率相同，因为只有一个答案）
    recall = accuracy
    
    return {
        "chapter_match_accuracy": accuracy,
        "chapter_match_recall": recall,
        "chapter_matched": accuracy == 1.0,
        "answer_chapter": answer_chapter or "",
        "reference_chapter": reference_chapter or "",
    }


async def evaluate_ragas(
    test_case: TestCase,
    ragas_evaluator: RagasEvaluator,
    ragas_metrics_config: Optional[Dict[str, bool]] = None,
) -> Dict:
    """
    使用 Ragas 进行 AI 评测（基于论文优化）
    
    根据论文2407.12873v1，重点关注：
    - Factual Correctness（事实正确性）
    - Faithfulness（忠实度）
    
    Args:
        test_case: 测试用例
        ragas_evaluator: Ragas 评测器实例
    
    Returns:
        Ragas 评测结果
    """
    try:
        # 优先使用检索上下文，如果没有则使用 reference
        context = test_case.retrieved_context if test_case.retrieved_context else test_case.reference
        
        # 综合评测（使用新的基于论文的指标）
        ragas_results = await ragas_evaluator.evaluate_comprehensive(
            question=test_case.question,
            answer=test_case.answer,
            reference=test_case.reference,  # 章节信息
            context=context,  # 使用实际检索上下文
            ground_truth=test_case.reference  # 将reference作为ground_truth用于Factual Correctness
        )
        
        # 提取核心指标（基于论文的建议）
        factual_result = ragas_results.get("factual_correctness", {})
        faithfulness_result = ragas_results.get("faithfulness", {})
        context_relevance_result = ragas_results.get("context_relevance", {})
        answer_relevancy_result = ragas_results.get("answer_relevancy", {})
        
        # 提取faithfulness的中间变量
        faithfulness_statements = faithfulness_result.get("statements", []) if faithfulness_result else []
        faithfulness_verdicts = faithfulness_result.get("verdicts", []) if faithfulness_result else []
        faithfulness_faithful_count = faithfulness_result.get("faithful_count") if faithfulness_result else None
        faithfulness_total_count = faithfulness_result.get("total_count") if faithfulness_result else None
        
        result = {
            # 核心指标1：Factual Correctness（事实正确性）
            "ragas_factual_correctness_score": factual_result.get("score", None),
            "ragas_factual_correctness_reason": factual_result.get("reason", ""),
            "ragas_factual_correctness_tp": factual_result.get("num_tp", None),
            "ragas_factual_correctness_fp": factual_result.get("num_fp", None),
            "ragas_factual_correctness_fn": factual_result.get("num_fn", None),
            
            # 核心指标2：Faithfulness（忠实度）
            "ragas_faithfulness_score": faithfulness_result.get("score", None) if faithfulness_result else None,
            "ragas_faithfulness_reason": faithfulness_result.get("reason", "") if faithfulness_result else None,
            
            # Faithfulness中间变量（新增）
            "ragas_faithfulness_statements": json.dumps(faithfulness_statements, ensure_ascii=False) if faithfulness_statements else None,
            "ragas_faithfulness_verdicts": json.dumps(faithfulness_verdicts, ensure_ascii=False) if faithfulness_verdicts else None,
            "ragas_faithfulness_faithful_count": faithfulness_faithful_count,
            "ragas_faithfulness_total_count": faithfulness_total_count,
            
            # 辅助指标：Context Relevance（上下文相关性）
            "ragas_context_relevance_score": context_relevance_result.get("score", None) if context_relevance_result else None,
            "ragas_context_relevance_reason": context_relevance_result.get("reason", "") if context_relevance_result else None,
            
            # 辅助指标：Answer Relevancy（答案相关性）
            "ragas_relevancy_score": answer_relevancy_result.get("score", 0.0) if answer_relevancy_result else None,
            "ragas_relevancy_reason": answer_relevancy_result.get("reason", "") if answer_relevancy_result else None,
            
            # 综合得分（基于核心指标：Factual Correctness + Faithfulness）
            "ragas_core_score": ragas_results.get("core_score", None),
            "ragas_overall_score": ragas_results.get("overall_score", 0.0),
            "ragas_primary_metrics": ragas_results.get("primary_metrics", []),
            "ragas_metrics_used": ragas_results.get("metrics_used", []),
        }
        
        # 兼容旧字段名（保持向后兼容）
        result["ragas_quality_score"] = factual_result.get("score", 0.0) if factual_result else None
        result["ragas_quality_reason"] = factual_result.get("reason", "") if factual_result else ""
        
        return result
    except Exception as e:
        logger.error(f"Ragas 评测失败: {e}", exc_info=True)
        return {
            "ragas_factual_correctness_score": None,
            "ragas_factual_correctness_reason": f"评测失败: {str(e)}",
            "ragas_faithfulness_score": None,
            "ragas_faithfulness_reason": None,
            "ragas_context_relevance_score": None,
            "ragas_relevancy_score": None,
            "ragas_overall_score": 0.0,
            "ragas_core_score": None,
            "ragas_error": str(e),
            # 兼容旧字段
            "ragas_quality_score": None,
            "ragas_quality_reason": f"评测失败: {str(e)}",
        }


async def evaluate_single_case(
    test_case: TestCase,
    mode: EvaluationMode,
    ragas_evaluator: Optional[RagasEvaluator] = None,
    ragas_metrics_config: Optional[Dict[str, bool]] = None
) -> Dict:
    """
    评测单个测试用例
    
    Args:
        test_case: 测试用例
        mode: 评测模式（chapter_match, ragas, hybrid）
        ragas_evaluator: Ragas 评测器（ragas 或 hybrid 模式需要）
        ragas_metrics_config: Ragas指标配置字典，用于选择性禁用某些指标
    
    Returns:
        评测结果字典
    """
    result = {
        "question": test_case.question,
        "answer": test_case.answer,
        "answer_chapter": test_case.answer_chapter,
        "reference": test_case.reference,
        "type": test_case.type,
        "theme": test_case.theme,
        "retrieved_context": test_case.retrieved_context,
        "retrieval_time": test_case.retrieval_time,
        "generation_time": test_case.generation_time,
        "total_time": test_case.total_time,
    }
    
    # 计算召回率@K（检索优化指标）
    if test_case.retrieved_chunks_json and test_case.reference:
        reference_chapter = ChapterMatcher.extract_chapter_info(test_case.reference)
        if reference_chapter:
            result["recall_at_3"] = calculate_recall_at_k_from_chunks(
                test_case.retrieved_chunks_json, reference_chapter, 3
            )
            result["recall_at_5"] = calculate_recall_at_k_from_chunks(
                test_case.retrieved_chunks_json, reference_chapter, 5
            )
            result["recall_at_10"] = calculate_recall_at_k_from_chunks(
                test_case.retrieved_chunks_json, reference_chapter, 10
            )
        else:
            result["recall_at_3"] = 0.0
            result["recall_at_5"] = 0.0
            result["recall_at_10"] = 0.0
    else:
        result["recall_at_3"] = 0.0
        result["recall_at_5"] = 0.0
        result["recall_at_10"] = 0.0
    
    # 章节匹配评测
    if mode in ("chapter_match", "hybrid"):
        chapter_result = evaluate_chapter_match(test_case)
        result.update(chapter_result)
    
    # Ragas AI 评测
    if mode in ("ragas", "hybrid"):
        if not ragas_evaluator:
            logger.warning("Ragas 模式需要 ragas_evaluator，但未提供，跳过 Ragas 评测")
            result.update({
                "ragas_quality_score": None,
                "ragas_relevancy_score": None,
                "ragas_overall_score": None,
            })
        else:
            ragas_result = await evaluate_ragas(test_case, ragas_evaluator, ragas_metrics_config)
            result.update(ragas_result)
    
    # 混合模式：计算综合得分
    if mode == "hybrid":
        chapter_accuracy = result.get("chapter_match_accuracy", 0.0) or 0.0
        ragas_score = result.get("ragas_overall_score")
        if ragas_score is None:
            ragas_score = 0.0
        # 综合得分：章节匹配权重 0.4，Ragas 权重 0.6
        result["hybrid_score"] = chapter_accuracy * 0.4 + ragas_score * 0.6
        result["hybrid_matched"] = chapter_accuracy == 1.0 and ragas_score >= 0.7
    
    return result


async def run_evaluation(
    csv_path: str,
    output_dir: Optional[str] = None,
    mode: EvaluationMode = "hybrid",
    progress_callback: Optional[Callable[[int, int, Dict], Awaitable[None]]] = None,
    ragas_metrics_config: Optional[Dict[str, bool]] = None,
) -> Dict:
    """
    运行评测流程：读取带答案的 CSV，计算指标，生成报告
    
    Args:
        csv_path: 包含答案的 CSV 文件路径
        output_dir: 输出目录（可选）
        mode: 评测模式（chapter_match, ragas, hybrid）
        progress_callback: 进度回调函数
        ragas_metrics_config: Ragas指标配置字典，例如：
            {
                "disable_faithfulness": False,  # 是否禁用忠实度评测
                "disable_factual_correctness": False,  # 是否禁用事实正确性评测
                "disable_context_relevance": False,  # 是否禁用上下文相关性评测
                "disable_answer_relevancy": False,  # 是否禁用答案相关性评测
            }
    
    Returns:
        Dict with results: {report_path, summary, total_questions, ...}
    """
    start_time = time.time()
    logger.info("=" * 80)
    logger.info(f"开始评测任务: {csv_path}")
    logger.info(f"评测模式: {mode}")
    logger.info("=" * 80)
    
    # 加载测试用例
    load_start = time.time()
    test_cases = load_test_cases_from_csv(csv_path)
    total = len(test_cases)
    load_time = time.time() - load_start
    logger.info(f"加载测试用例完成: {total} 条，耗时 {load_time:.2f} 秒")
    
    if total == 0:
        raise ValueError("CSV 文件中没有测试用例")
    
    # 初始化 Ragas 评测器（如果需要）
    ragas_init_start = time.time()
    ragas_evaluator = None
    ragas_init_error = None
    if mode in ("ragas", "hybrid"):
        logger.info("开始初始化 Ragas 评测器...")
        try:
            ragas_evaluator = RagasEvaluator()
            ragas_init_time = time.time() - ragas_init_start
            logger.info(f"✅ Ragas 评测器初始化成功，使用模式: {mode}，耗时 {ragas_init_time:.2f}秒 ({ragas_init_time/60:.2f}分钟)")
        except Exception as e:
            ragas_init_time = time.time() - ragas_init_start
            logger.error(f"初始化 Ragas 评测器失败（耗时 {ragas_init_time:.2f}秒）: {e}", exc_info=True)
            ragas_init_error = str(e)
            if mode == "ragas":
                raise ValueError(f"Ragas 模式需要 Ragas 评测器，但初始化失败: {e}")
            else:
                logger.warning(f"混合模式：Ragas 评测器初始化失败，将只使用章节匹配。错误: {e}")
                # 不改变 mode，保持为 "hybrid"，但在 summary 中记录错误
    else:
        ragas_init_time = 0.0
    
    # 设置输出目录 - 保存到 data/evaluation/ 目录
    if output_dir is None:
        evaluation_dir = DATA_EVALUATION_DIR
        evaluation_dir.mkdir(parents=True, exist_ok=True)
        csv_path_obj = Path(csv_path)
        output_dir = evaluation_dir / f"{csv_path_obj.stem}_evaluation_results_{mode}"
    else:
        output_dir = Path(output_dir)
    
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 评测所有用例（支持并发）
    evaluation_start_time = time.time()
    
    # 读取并发配置（默认5，可以通过环境变量配置，最多不超过LLM_MAX_CONCURRENT）
    # 如果有多个worker，可以设置为worker数量以充分利用资源
    max_concurrent = int(os.getenv("EVAL_MAX_CONCURRENT", "5"))
    llm_max_concurrent = int(os.getenv("LLM_MAX_CONCURRENT", "10"))
    effective_max_concurrent = min(max_concurrent, llm_max_concurrent)
    
    logger.info(f"评测并发配置: max_concurrent={effective_max_concurrent} (配置值: {max_concurrent}, LLM限制: {llm_max_concurrent})")
    
    # 使用信号量控制并发数
    semaphore = asyncio.Semaphore(effective_max_concurrent)
    results = []
    retrieval_stats = {
        "total_with_context": 0,
        "total_without_context": 0,
        "retrieval_success_rate": 0.0,
    }
    
    # 已完成的任务计数（用于进度跟踪）
    completed_count = 0
    completed_lock = asyncio.Lock()
    
    async def evaluate_with_semaphore(idx: int, test_case: TestCase) -> tuple[int, Dict]:
        """使用信号量控制的并发评测函数"""
        nonlocal completed_count
        async with semaphore:
            try:
                case_start = time.time()
                logger.debug(f"开始评测问题 {idx}/{total}: {test_case.question[:60]}... (模式: {mode})")
                result = await evaluate_single_case(test_case, mode, ragas_evaluator, ragas_metrics_config)
                case_time = time.time() - case_start
                
                # 更新检索统计（需要加锁）
                async with completed_lock:
                    completed_count += 1
                    if test_case.retrieved_context:
                        retrieval_stats["total_with_context"] += 1
                    else:
                        retrieval_stats["total_without_context"] += 1
                    
                    # 记录每个问题的评测耗时（INFO级别，便于监控）
                    elapsed_total = time.time() - evaluation_start_time
                    avg_time_so_far = elapsed_total / completed_count if completed_count > 0 else 0
                    remaining = total - completed_count
                    eta = avg_time_so_far * remaining if remaining > 0 else 0
                    
                    if mode in ("ragas", "hybrid") and "ragas_relevancy_score" in result:
                        relevancy = result.get("ragas_relevancy_score", 0.0)
                        logger.info(f"[{completed_count}/{total}] 评测完成 | 耗时: {case_time:.2f}s | 相关性: {relevancy:.2f} | "
                                  f"累计: {elapsed_total:.1f}s | 平均: {avg_time_so_far:.2f}s/条 | 预计剩余: {eta:.1f}s")
                    elif mode in ("chapter_match", "hybrid") and "chapter_matched" in result:
                        matched = result.get("chapter_matched", False)
                        logger.info(f"[{completed_count}/{total}] 评测完成 | 耗时: {case_time:.2f}s | 章节匹配: {'✅' if matched else '❌'} | "
                                  f"累计: {elapsed_total:.1f}s | 平均: {avg_time_so_far:.2f}s/条 | 预计剩余: {eta:.1f}s")
                    else:
                        logger.info(f"[{completed_count}/{total}] 评测完成 | 耗时: {case_time:.2f}s | "
                                  f"累计: {elapsed_total:.1f}s | 平均: {avg_time_so_far:.2f}s/条 | 预计剩余: {eta:.1f}s")
                    
                    # 发送进度更新
                    if progress_callback:
                        try:
                            # 传递 completed_count 作为当前完成数（不是 completed_count - 1）
                            await progress_callback(
                                completed_count,
                                total,
                                {"status": "evaluating", "current": completed_count, "total": total, "mode": mode}
                            )
                        except Exception as e:
                            # 进度回调失败不应中断评测
                            logger.warning(f"进度回调失败（不影响评测）: {e}")
                    
                    # 每完成10%或最后一条时记录汇总进度日志
                    if completed_count % max(1, total // 10) == 0 or completed_count == total:
                        elapsed = time.time() - evaluation_start_time
                        avg_time_per_item = elapsed / completed_count if completed_count > 0 else 0
                        remaining = total - completed_count
                        eta = avg_time_per_item * remaining if remaining > 0 else 0
                        percentage = completed_count * 100 // total
                        logger.info(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                        logger.info(f"评测进度汇总: {completed_count}/{total} ({percentage}%) | "
                                  f"已用时: {elapsed:.1f}s ({elapsed/60:.1f}分钟) | "
                                  f"平均: {avg_time_per_item:.2f}s/条 | "
                                  f"预计剩余: {eta:.1f}s ({eta/60:.1f}分钟)")
                        logger.info(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
                
                return (idx, result)
            except Exception as e:
                case_time = time.time() - case_start if 'case_start' in locals() else 0
                logger.error(f"评测用例 {idx} 失败（耗时 {case_time:.2f}s）: {e}", exc_info=True)
                # 添加错误结果
                error_result = {
                    "question": test_case.question,
                    "answer": test_case.answer,
                    "answer_chapter": test_case.answer_chapter,
                    "reference": test_case.reference,
                    "type": test_case.type,
                    "theme": test_case.theme,
                    "retrieved_context": test_case.retrieved_context,
                    "error": str(e)
                }
                async with completed_lock:
                    completed_count += 1
                    if test_case.retrieved_context:
                        retrieval_stats["total_with_context"] += 1
                    else:
                        retrieval_stats["total_without_context"] += 1
                return (idx, error_result)
    
    # 创建所有评测任务
    tasks = [evaluate_with_semaphore(idx, test_case) for idx, test_case in enumerate(test_cases, 1)]
    
    # 并发执行所有任务
    logger.info(f"开始并发评测 {total} 个用例，并发数: {effective_max_concurrent}")
    task_results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # 按照原始顺序整理结果
    indexed_results = []
    for result in task_results:
        if isinstance(result, Exception):
            logger.error(f"评测任务执行异常: {result}", exc_info=True)
            # 创建一个占位错误结果
            indexed_results.append((0, {"error": str(result)}))
        else:
            indexed_results.append(result)
    
    # 按索引排序（保持原始顺序）
    indexed_results.sort(key=lambda x: x[0])
    results = [result for _, result in indexed_results]
    
    evaluation_time = time.time() - evaluation_start_time
    logger.info("=" * 80)
    logger.info(f"✅ 评测处理完成: 耗时 {evaluation_time:.2f}秒 ({evaluation_time/60:.2f}分钟)")
    logger.info("=" * 80)
    
    # 转换为 DataFrame
    df = pd.DataFrame(results)
    
    # 计算总体指标
    total_questions = len(results)
    
    summary = {
        "total_questions": total_questions,
        "mode": mode,
    }
    
    # 检索质量统计
    if total_questions > 0:
        retrieval_stats["retrieval_success_rate"] = retrieval_stats["total_with_context"] / total_questions
    summary.update({
        "retrieval_with_context_count": retrieval_stats["total_with_context"],
        "retrieval_without_context_count": retrieval_stats["total_without_context"],
        "retrieval_success_rate": float(retrieval_stats["retrieval_success_rate"]),
        "retrieval_success_rate_percentage": float(retrieval_stats["retrieval_success_rate"] * 100),
    })
    
    # 召回率@K统计（检索优化指标）
    if "recall_at_3" in df.columns:
        recall_at_3_scores = df["recall_at_3"].dropna()
        if len(recall_at_3_scores) > 0:
            summary["recall_at_3"] = float(recall_at_3_scores.mean())
            summary["recall_at_3_percentage"] = float(recall_at_3_scores.mean() * 100)
    
    if "recall_at_5" in df.columns:
        recall_at_5_scores = df["recall_at_5"].dropna()
        if len(recall_at_5_scores) > 0:
            summary["recall_at_5"] = float(recall_at_5_scores.mean())
            summary["recall_at_5_percentage"] = float(recall_at_5_scores.mean() * 100)
    
    if "recall_at_10" in df.columns:
        recall_at_10_scores = df["recall_at_10"].dropna()
        if len(recall_at_10_scores) > 0:
            summary["recall_at_10"] = float(recall_at_10_scores.mean())
            summary["recall_at_10_percentage"] = float(recall_at_10_scores.mean() * 100)
    
    # 性能指标统计
    if "retrieval_time" in df.columns:
        retrieval_times = df["retrieval_time"].dropna()
        if len(retrieval_times) > 0:
            summary["avg_retrieval_time"] = float(retrieval_times.mean())
            summary["p50_retrieval_time"] = float(retrieval_times.quantile(0.5))
            summary["p95_retrieval_time"] = float(retrieval_times.quantile(0.95))
    
    if "generation_time" in df.columns:
        generation_times = df["generation_time"].dropna()
        if len(generation_times) > 0:
            summary["avg_generation_time"] = float(generation_times.mean())
            summary["p50_generation_time"] = float(generation_times.quantile(0.5))
            summary["p95_generation_time"] = float(generation_times.quantile(0.95))
    
    if "total_time" in df.columns:
        total_times = df["total_time"].dropna()
        if len(total_times) > 0:
            summary["avg_total_time"] = float(total_times.mean())
            summary["p50_total_time"] = float(total_times.quantile(0.5))
            summary["p95_total_time"] = float(total_times.quantile(0.95))
            # 并发10条的平均时间（占位符，实际需要并发测试）
            summary["concurrent_10_avg_time"] = float(total_times.mean())
    
    # 记录 Ragas 初始化错误（如果有）
    if ragas_init_error:
        summary["ragas_init_error"] = ragas_init_error
        summary["ragas_available"] = False
    else:
        summary["ragas_available"] = mode in ("ragas", "hybrid")
    
    # 章节匹配指标
    if mode in ("chapter_match", "hybrid"):
        if "chapter_match_accuracy" in df.columns:
            chapter_accuracy_avg = df["chapter_match_accuracy"].mean() if len(df) > 0 else 0.0
            chapter_recall_avg = df["chapter_match_recall"].mean() if len(df) > 0 else 0.0
            chapter_correct_count = df["chapter_matched"].sum() if "chapter_matched" in df.columns else 0
            
            summary.update({
                "chapter_match_correct_count": int(chapter_correct_count),
                "chapter_match_accuracy": float(chapter_accuracy_avg),
                "chapter_match_recall": float(chapter_recall_avg),
                "chapter_match_accuracy_percentage": float(chapter_accuracy_avg * 100),
                "chapter_match_recall_percentage": float(chapter_recall_avg * 100),
            })
    
    # Ragas 指标（基于论文优化：重点关注Factual Correctness和Faithfulness）
    if mode in ("ragas", "hybrid"):
        if ragas_evaluator is None:
            # Ragas 初始化失败，设置默认值
            summary.update({
                "ragas_overall_score": None,
                "ragas_overall_score_percentage": None,
                "ragas_core_score": None,
                "ragas_factual_correctness_score": None,
                "ragas_faithfulness_score": None,
                "ragas_context_relevance_score": None,
                "ragas_relevancy_score": None,
                "ragas_quality_score": None,  # 兼容旧字段
            })
        else:
            # 核心指标1：Factual Correctness（事实正确性）
            if "ragas_factual_correctness_score" in df.columns:
                factual_scores = df["ragas_factual_correctness_score"].dropna()
                if len(factual_scores) > 0:
                    factual_avg = factual_scores.mean()
                    summary["ragas_factual_correctness_score"] = float(factual_avg)
                    summary["ragas_factual_correctness_score_percentage"] = float(factual_avg * 100)
                    
                    # TP/FP/FN统计
                    if "ragas_factual_correctness_tp" in df.columns:
                        tp_values = df["ragas_factual_correctness_tp"].dropna()
                        fp_values = df["ragas_factual_correctness_fp"].dropna()
                        fn_values = df["ragas_factual_correctness_fn"].dropna()
                        if len(tp_values) > 0:
                            summary["ragas_factual_correctness_avg_tp"] = float(tp_values.mean())
                            summary["ragas_factual_correctness_avg_fp"] = float(fp_values.mean()) if len(fp_values) > 0 else 0.0
                            summary["ragas_factual_correctness_avg_fn"] = float(fn_values.mean()) if len(fn_values) > 0 else 0.0
            
            # 核心指标2：Faithfulness（忠实度）
            if "ragas_faithfulness_score" in df.columns:
                faithfulness_scores = df["ragas_faithfulness_score"].dropna()
                if len(faithfulness_scores) > 0:
                    faithfulness_avg = faithfulness_scores.mean()
                    summary["ragas_faithfulness_score"] = float(faithfulness_avg)
                    summary["ragas_faithfulness_score_percentage"] = float(faithfulness_avg * 100)
            
            # Faithfulness中间变量统计
            if "ragas_faithfulness_total_count" in df.columns:
                total_counts = df["ragas_faithfulness_total_count"].dropna()
                if len(total_counts) > 0:
                    summary["ragas_faithfulness_statements_avg"] = float(total_counts.mean())
                    summary["ragas_faithfulness_statements_total"] = int(total_counts.sum())
            
            if "ragas_faithfulness_faithful_count" in df.columns and "ragas_faithfulness_total_count" in df.columns:
                faithful_counts = df["ragas_faithfulness_faithful_count"].dropna()
                total_counts = df["ragas_faithfulness_total_count"].dropna()
                if len(faithful_counts) > 0 and len(total_counts) > 0:
                    # 计算总体忠实比例
                    total_faithful = int(faithful_counts.sum())
                    total_statements = int(total_counts.sum())
                    if total_statements > 0:
                        summary["ragas_faithfulness_verdicts_faithful_ratio"] = float(total_faithful / total_statements)
                        summary["ragas_faithfulness_verdicts_faithful_ratio_percentage"] = float(total_faithful / total_statements * 100)
                        summary["ragas_faithfulness_statements_distribution"] = {
                            "faithful_count": int(total_faithful),
                            "unfaithful_count": int(total_statements - total_faithful),
                            "total_count": int(total_statements)
                        }
            
            # 辅助指标：Context Relevance（上下文相关性）
            if "ragas_context_relevance_score" in df.columns:
                context_relevance_scores = df["ragas_context_relevance_score"].dropna()
                if len(context_relevance_scores) > 0:
                    context_relevance_avg = context_relevance_scores.mean()
                    summary["ragas_context_relevance_score"] = float(context_relevance_avg)
                    summary["ragas_context_relevance_score_percentage"] = float(context_relevance_avg * 100)
            
            # 辅助指标：Answer Relevancy（答案相关性）
            if "ragas_relevancy_score" in df.columns:
                relevancy_scores = df["ragas_relevancy_score"].dropna()
                if len(relevancy_scores) > 0:
                    relevancy_avg = relevancy_scores.mean()
                    summary["ragas_relevancy_score"] = float(relevancy_avg)
                    summary["ragas_relevancy_score_percentage"] = float(relevancy_avg * 100)
                    summary["ragas_satisfaction_score"] = float(relevancy_avg)  # 用户满意度 = 相关性
                    summary["ragas_satisfaction_score_percentage"] = float(relevancy_avg * 100)
            
            # 核心得分（Factual Correctness + Faithfulness的平均值）
            if "ragas_core_score" in df.columns:
                core_scores = df["ragas_core_score"].dropna()
                if len(core_scores) > 0:
                    core_avg = core_scores.mean()
                    summary["ragas_core_score"] = float(core_avg)
                    summary["ragas_core_score_percentage"] = float(core_avg * 100)
            
            # 综合得分（overall_score）
            if "ragas_overall_score" in df.columns:
                ragas_scores = df["ragas_overall_score"].dropna()
                if len(ragas_scores) > 0:
                    ragas_avg = ragas_scores.mean()
                    summary.update({
                        "ragas_overall_score": float(ragas_avg),
                        "ragas_overall_score_percentage": float(ragas_avg * 100),
                    })
            
            # 兼容旧字段：quality_score（映射到factual_correctness）
            if "ragas_quality_score" in df.columns:
                quality_scores = df["ragas_quality_score"].dropna()
                if len(quality_scores) > 0:
                    summary["ragas_quality_score"] = float(quality_scores.mean())
                    summary["ragas_quality_score_percentage"] = float(quality_scores.mean() * 100)
            
            # 按问题类型（S1-S6）的指标分布统计
            if "type" in df.columns:
                for metric_col in ["ragas_factual_correctness_score", "ragas_faithfulness_score", "ragas_relevancy_score"]:
                    if metric_col in df.columns:
                        type_metrics = {}
                        for q_type in ["S1", "S2", "S3", "S4", "S5", "S6"]:
                            type_df = df[df["type"].str.contains(q_type, na=False, case=False)]
                            if len(type_df) > 0:
                                type_scores = type_df[metric_col].dropna()
                                if len(type_scores) > 0:
                                    metric_name = metric_col.replace("ragas_", "").replace("_score", "")
                                    type_metrics[f"{q_type}_{metric_name}_score"] = float(type_scores.mean())
                                    type_metrics[f"{q_type}_{metric_name}_score_percentage"] = float(type_scores.mean() * 100)
                                    type_metrics[f"{q_type}_count"] = int(len(type_scores))
                        
                        if type_metrics:
                            summary.update(type_metrics)
    
    # 混合模式指标
    if mode == "hybrid":
        if "hybrid_score" in df.columns:
            hybrid_scores = df["hybrid_score"].dropna()
            if len(hybrid_scores) > 0:
                summary["hybrid_score"] = float(hybrid_scores.mean())
                summary["hybrid_score_percentage"] = float(hybrid_scores.mean() * 100)
        
        if "hybrid_matched" in df.columns:
            hybrid_correct_count = df["hybrid_matched"].sum()
            summary["hybrid_correct_count"] = int(hybrid_correct_count)
    
    # 泛化性指标计算
    # 使用混合得分或Ragas得分作为基础得分
    # 优先使用核心得分（Factual Correctness + Faithfulness）
    score_column = None
    if mode == "hybrid" and "hybrid_score" in df.columns:
        score_column = "hybrid_score"
    elif mode in ("ragas", "hybrid") and "ragas_core_score" in df.columns:
        score_column = "ragas_core_score"  # 优先使用核心得分
    elif mode in ("ragas", "hybrid") and "ragas_overall_score" in df.columns:
        score_column = "ragas_overall_score"
    elif mode in ("chapter_match", "hybrid") and "chapter_match_accuracy" in df.columns:
        score_column = "chapter_match_accuracy"
    
    if score_column and "type" in df.columns:
        # 按问题类型分组统计得分
        results_by_type = {}
        for q_type in ["S1", "S2", "S3", "S4", "S5", "S6"]:
            type_df = df[df["type"].str.contains(q_type, na=False, case=False)]
            if len(type_df) > 0:
                type_scores = type_df[score_column].dropna().tolist()
                if type_scores:
                    results_by_type[q_type] = type_scores
        
        if len(results_by_type) >= 2:  # 至少需要2种类型才能计算泛化性
            generalization_score = calculate_generalization_score(results_by_type)
            summary["generalization_score"] = float(generalization_score)
            summary["generalization_score_percentage"] = float(generalization_score * 100)
            
            # 记录各类型问题的平均得分（用于分析）
            type_avg_scores = {}
            for q_type, scores in results_by_type.items():
                if scores:
                    type_avg_scores[f"{q_type}_avg_score"] = float(np.mean(scores))
            if type_avg_scores:
                summary.update(type_avg_scores)
    
    # 生成优化建议（基于论文的核心指标）
    optimization_suggestions = []
    
    # 核心指标1：Factual Correctness（事实正确性）
    if "ragas_factual_correctness_score" in summary:
        factual = summary.get("ragas_factual_correctness_score", 0.0) or 0.0
        if factual < 0.7:
            optimization_suggestions.append({
                "category": "答案质量优化",
                "metric": "事实正确性（Factual Correctness）",
                "current_value": f"{factual:.2%}",
                "suggestion": "事实正确性得分较低，建议优化生成模型或提示词，确保答案包含正确事实，减少错误事实和遗漏"
            })
    
    # 核心指标2：Faithfulness（忠实度）
    if "ragas_faithfulness_score" in summary:
        faithfulness = summary.get("ragas_faithfulness_score", 0.0) or 0.0
        if faithfulness < 0.7:
            optimization_suggestions.append({
                "category": "答案质量优化",
                "metric": "忠实度（Faithfulness）",
                "current_value": f"{faithfulness:.2%}",
                "suggestion": "忠实度得分较低，说明答案可能包含上下文之外的信息或编造内容，建议优化生成模型，确保答案基于提供的上下文"
            })
    
    # 辅助指标：Context Relevance（上下文相关性）
    if "ragas_context_relevance_score" in summary:
        context_relevance = summary.get("ragas_context_relevance_score", 0.0) or 0.0
        if context_relevance < 0.7:
            optimization_suggestions.append({
                "category": "检索优化",
                "metric": "上下文相关性（Context Relevance）",
                "current_value": f"{context_relevance:.2%}",
                "suggestion": "上下文相关性得分较低，建议优化检索系统：检查嵌入模型、调整top_k参数、优化查询理解"
            })
    
    # 辅助指标：Answer Relevancy（答案相关性）
    if "ragas_relevancy_score" in summary:
        relevancy = summary.get("ragas_relevancy_score", 0.0) or 0.0
        if relevancy < 0.7:
            optimization_suggestions.append({
                "category": "提示词优化",
                "metric": "答案相关性（Answer Relevancy）",
                "current_value": f"{relevancy:.2%}",
                "suggestion": "答案相关性得分较低，建议优化提示词工程，提高答案与问题的相关性，减少无关信息"
            })
    
    # 兼容旧字段
    if "ragas_quality_score" in summary:
        quality = summary.get("ragas_quality_score", 0.0) or 0.0
        if quality < 0.7:
            optimization_suggestions.append({
                "category": "答案质量优化",
                "metric": "答案质量得分",
                "current_value": f"{quality:.2%}",
                "suggestion": "答案质量得分较低，建议优化提示词，提高答案的准确性、完整性和一致性"
            })
    
    # 检索优化建议
    if "recall_at_10" in summary:
        recall_10 = summary.get("recall_at_10", 0.0) or 0.0
        if recall_10 < 0.8:
            optimization_suggestions.append({
                "category": "检索优化",
                "metric": "召回率@10",
                "current_value": f"{recall_10:.2%}",
                "suggestion": "召回率@10较低，建议优化检索系统：检查嵌入模型、调整top_k参数、降低相似度阈值"
            })
    
    if "recall_at_5" in summary:
        recall_5 = summary.get("recall_at_5", 0.0) or 0.0
        if recall_5 < 0.7:
            optimization_suggestions.append({
                "category": "检索优化",
                "metric": "召回率@5",
                "current_value": f"{recall_5:.2%}",
                "suggestion": "召回率@5较低，建议优化检索系统：考虑使用重排序模型、调整向量相似度权重"
            })
    
    # 泛化性优化建议
    if "generalization_score" in summary:
        generalization = summary.get("generalization_score", 0.0) or 0.0
        if generalization < 0.6:
            optimization_suggestions.append({
                "category": "系统优化",
                "metric": "泛化性得分",
                "current_value": f"{generalization:.2%}",
                "suggestion": "泛化性得分较低，系统对不同类型问题的处理能力不均衡，建议检查特定类型问题的处理逻辑"
            })
    
    if optimization_suggestions:
        summary["optimization_suggestions"] = optimization_suggestions
    
    # 计算总时间（在保存前添加到summary）
    total_time = time.time() - start_time
    summary["total_time"] = total_time
    summary["load_time"] = load_time
    summary["ragas_init_time"] = ragas_init_time if mode in ("ragas", "hybrid") else 0
    summary["evaluation_time"] = evaluation_time
    summary["avg_time_per_question"] = total_time / total if total > 0 else 0
    
    # 保存结果
    save_start = time.time()
    results_csv_path = output_dir / "evaluation_results.csv"
    df.to_csv(results_csv_path, index=False, encoding="utf-8-sig")
    
    summary_json_path = output_dir / "evaluation_summary.json"
    with open(summary_json_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    save_time = time.time() - save_start
    logger.info(f"保存结果完成: 耗时 {save_time:.2f} 秒")
    
    # 保存时间也需要添加到summary（保存后更新）
    summary["save_time"] = save_time
    # 重新保存summary以包含save_time（可选，因为save_time很小）
    # 为了完整性，我们更新summary但不再重新保存JSON（避免重复IO）
    
    result = {
        "results_csv_path": str(results_csv_path),
        "summary_json_path": str(summary_json_path),
        "summary": summary,
        "total_questions": total_questions,
        "total_time": total_time,
        "mode": mode,
    }
    
    logger.info("=" * 80)
    logger.info(f"评测任务完成（模式: {mode}）!")
    logger.info(f"  总问题数: {total_questions}")
    logger.info(f"  时间统计:")
    logger.info("=" * 80)
    logger.info("⏱️  时间统计汇总:")
    logger.info(f"  • 加载CSV: {load_time:.2f}s ({load_time/60:.2f}分钟)")
    if mode in ("ragas", "hybrid"):
        logger.info(f"  • Ragas初始化: {ragas_init_time:.2f}s ({ragas_init_time/60:.2f}分钟)")
    logger.info(f"  • 评测处理: {evaluation_time:.2f}s ({evaluation_time/60:.2f}分钟)")
    logger.info(f"  • 保存结果: {save_time:.2f}s ({save_time/60:.2f}分钟)")
    logger.info(f"  • 总计: {total_time:.2f}s ({total_time/60:.2f}分钟)")
    if total_questions > 0:
        avg_per_question = total_time / total_questions
        logger.info(f"  • 平均每题: {avg_per_question:.2f}s")
        logger.info(f"  • 预计速度: {3600/avg_per_question:.1f}题/小时")
    logger.info("=" * 80)
    logger.info(f"  评测结果:")
    if mode in ("chapter_match", "hybrid"):
        chapter_pct = summary.get('chapter_match_accuracy_percentage') or 0
        logger.info(f"    章节匹配准确率: {chapter_pct:.2f}%")
    if mode in ("ragas", "hybrid"):
        # 优先显示核心指标（基于论文的建议）
        core_pct = summary.get('ragas_core_score_percentage')
        if core_pct is not None:
            logger.info(f"    Ragas 核心得分（Factual Correctness + Faithfulness）: {core_pct:.2f}%")
        factual_pct = summary.get('ragas_factual_correctness_score_percentage')
        if factual_pct is not None:
            logger.info(f"      事实正确性（Factual Correctness）: {factual_pct:.2f}%")
        faithfulness_pct = summary.get('ragas_faithfulness_score_percentage')
        if faithfulness_pct is not None:
            logger.info(f"      忠实度（Faithfulness）: {faithfulness_pct:.2f}%")
        ragas_pct = summary.get('ragas_overall_score_percentage') or 0
        logger.info(f"    Ragas 综合得分: {ragas_pct:.2f}%")
    if mode == "hybrid":
        hybrid_pct = summary.get('hybrid_score_percentage') or 0
        logger.info(f"    混合综合得分: {hybrid_pct:.2f}%")
    logger.info("=" * 80)
    
    return result
