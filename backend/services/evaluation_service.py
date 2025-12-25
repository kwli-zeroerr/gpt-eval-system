"""
评测服务 - 支持章节匹配和 Ragas AI 评测的混合评测系统
"""
import csv
import json
import logging
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
    ragas_evaluator: RagasEvaluator
) -> Dict:
    """
    使用 Ragas 进行 AI 评测
    
    Args:
        test_case: 测试用例
        ragas_evaluator: Ragas 评测器实例
    
    Returns:
        Ragas 评测结果
    """
    try:
        # 优先使用检索上下文，如果没有则使用 reference
        context = test_case.retrieved_context if test_case.retrieved_context else test_case.reference
        
        # 综合评测
        ragas_results = await ragas_evaluator.evaluate_comprehensive(
            question=test_case.question,
            answer=test_case.answer,
            reference=test_case.reference,
            context=context  # 使用实际检索上下文而非 reference
        )
        
        return {
            "ragas_quality_score": ragas_results.get("quality", {}).get("score", 0.0),
            "ragas_quality_reason": ragas_results.get("quality", {}).get("reason", ""),
            "ragas_relevancy_score": ragas_results.get("relevancy", {}).get("score", 0.0),
            "ragas_relevancy_reason": ragas_results.get("relevancy", {}).get("reason", ""),
            "ragas_faithfulness_score": ragas_results.get("faithfulness", {}).get("score", 0.0) if "faithfulness" in ragas_results else None,
            "ragas_faithfulness_reason": ragas_results.get("faithfulness", {}).get("reason", "") if "faithfulness" in ragas_results else None,
            "ragas_overall_score": ragas_results.get("overall_score", 0.0),
        }
    except Exception as e:
        logger.error(f"Ragas 评测失败: {e}", exc_info=True)
        return {
            "ragas_quality_score": 0.0,
            "ragas_quality_reason": f"评测失败: {str(e)}",
            "ragas_relevancy_score": 0.0,
            "ragas_relevancy_reason": f"评测失败: {str(e)}",
            "ragas_faithfulness_score": None,
            "ragas_faithfulness_reason": None,
            "ragas_overall_score": 0.0,
            "ragas_error": str(e)
        }


async def evaluate_single_case(
    test_case: TestCase,
    mode: EvaluationMode,
    ragas_evaluator: Optional[RagasEvaluator] = None
) -> Dict:
    """
    评测单个测试用例
    
    Args:
        test_case: 测试用例
        mode: 评测模式（chapter_match, ragas, hybrid）
        ragas_evaluator: Ragas 评测器（ragas 或 hybrid 模式需要）
    
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
            ragas_result = await evaluate_ragas(test_case, ragas_evaluator)
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
) -> Dict:
    """
    运行评测流程：读取带答案的 CSV，计算指标，生成报告
    
    Args:
        csv_path: 包含答案的 CSV 文件路径
        output_dir: 输出目录（可选）
        mode: 评测模式（chapter_match, ragas, hybrid）
        progress_callback: 进度回调函数
    
    Returns:
        Dict with results: {report_path, summary, total_questions, ...}
    """
    start_time = time.time()
    
    # 加载测试用例
    test_cases = load_test_cases_from_csv(csv_path)
    total = len(test_cases)
    
    if total == 0:
        raise ValueError("CSV 文件中没有测试用例")
    
    # 初始化 Ragas 评测器（如果需要）
    ragas_evaluator = None
    ragas_init_error = None
    if mode in ("ragas", "hybrid"):
        try:
            ragas_evaluator = RagasEvaluator()
            logger.info(f"Ragas 评测器初始化成功，使用模式: {mode}")
        except Exception as e:
            logger.error(f"初始化 Ragas 评测器失败: {e}", exc_info=True)
            ragas_init_error = str(e)
            if mode == "ragas":
                raise ValueError(f"Ragas 模式需要 Ragas 评测器，但初始化失败: {e}")
            else:
                logger.warning(f"混合模式：Ragas 评测器初始化失败，将只使用章节匹配。错误: {e}")
                # 不改变 mode，保持为 "hybrid"，但在 summary 中记录错误
    
    # 设置输出目录 - 保存到 data/evaluation/ 目录
    if output_dir is None:
        evaluation_dir = DATA_EVALUATION_DIR
        evaluation_dir.mkdir(parents=True, exist_ok=True)
        csv_path_obj = Path(csv_path)
        output_dir = evaluation_dir / f"{csv_path_obj.stem}_evaluation_results_{mode}"
    else:
        output_dir = Path(output_dir)
    
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 评测所有用例
    results = []
    retrieval_stats = {
        "total_with_context": 0,
        "total_without_context": 0,
        "retrieval_success_rate": 0.0,
    }
    
    for idx, test_case in enumerate(test_cases, 1):
        try:
            # 统计检索质量
            if test_case.retrieved_context:
                retrieval_stats["total_with_context"] += 1
            else:
                retrieval_stats["total_without_context"] += 1
            
            logger.info(f"开始评测问题 {idx}/{total}: {test_case.question[:60]}... (模式: {mode})")
            result = await evaluate_single_case(test_case, mode, ragas_evaluator)
            results.append(result)
            
            # 记录评测结果摘要
            if mode in ("ragas", "hybrid") and "ragas_relevancy_score" in result:
                relevancy = result.get("ragas_relevancy_score", 0.0)
                logger.info(f"问题 {idx}/{total} 评测完成: 相关性得分={relevancy:.2f}")
            elif mode in ("chapter_match", "hybrid") and "chapter_matched" in result:
                matched = result.get("chapter_matched", False)
                logger.info(f"问题 {idx}/{total} 评测完成: 章节匹配={'是' if matched else '否'}")
        except Exception as e:
            logger.error(f"评测用例 {idx} 失败: {e}", exc_info=True)
            # 添加错误结果
            results.append({
                "question": test_case.question,
                "answer": test_case.answer,
                "answer_chapter": test_case.answer_chapter,
                "reference": test_case.reference,
                "type": test_case.type,
                "theme": test_case.theme,
                "retrieved_context": test_case.retrieved_context,
                "error": str(e)
            })
        
        # 发送进度更新
        if progress_callback:
            await progress_callback(
                idx - 1,
                total,
                {"status": "evaluating", "current": idx, "total": total, "mode": mode}
            )
    
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
    
    # Ragas 指标
    if mode in ("ragas", "hybrid"):
        if ragas_evaluator is None:
            # Ragas 初始化失败，设置默认值
            summary.update({
                "ragas_overall_score": None,
                "ragas_overall_score_percentage": None,
                "ragas_quality_score": None,
                "ragas_relevancy_score": None,
                "ragas_satisfaction_score": None,
                "ragas_relevancy_score_percentage": None,
            })
        else:
            if "ragas_overall_score" in df.columns:
                ragas_scores = df["ragas_overall_score"].dropna()
                if len(ragas_scores) > 0:
                    ragas_avg = ragas_scores.mean()
                    summary.update({
                        "ragas_overall_score": float(ragas_avg),
                        "ragas_overall_score_percentage": float(ragas_avg * 100),
                    })
            
            if "ragas_quality_score" in df.columns:
                quality_scores = df["ragas_quality_score"].dropna()
                if len(quality_scores) > 0:
                    summary["ragas_quality_score"] = float(quality_scores.mean())
                    summary["ragas_quality_score_percentage"] = float(quality_scores.mean() * 100)
            
            if "ragas_relevancy_score" in df.columns:
                relevancy_scores = df["ragas_relevancy_score"].dropna()
                if len(relevancy_scores) > 0:
                    relevancy_avg = relevancy_scores.mean()
                    summary["ragas_relevancy_score"] = float(relevancy_avg)
                    summary["ragas_relevancy_score_percentage"] = float(relevancy_avg * 100)
                    summary["ragas_satisfaction_score"] = float(relevancy_avg)  # 用户满意度 = 相关性
                    summary["ragas_satisfaction_score_percentage"] = float(relevancy_avg * 100)
                    
                    # 相关性得分分布统计
                    excellent_count = len(relevancy_scores[relevancy_scores >= 0.8])
                    good_count = len(relevancy_scores[(relevancy_scores >= 0.6) & (relevancy_scores < 0.8)])
                    fair_count = len(relevancy_scores[(relevancy_scores >= 0.4) & (relevancy_scores < 0.6)])
                    poor_count = len(relevancy_scores[relevancy_scores < 0.4])
                    
                    summary.update({
                        "ragas_relevancy_excellent_count": int(excellent_count),
                        "ragas_relevancy_good_count": int(good_count),
                        "ragas_relevancy_fair_count": int(fair_count),
                        "ragas_relevancy_poor_count": int(poor_count),
                    })
            
            # 按问题类型（S1-S6）的相关性分布统计
            if "type" in df.columns and "ragas_relevancy_score" in df.columns:
                type_relevancy = {}
                for q_type in ["S1", "S2", "S3", "S4", "S5", "S6"]:
                    type_df = df[df["type"].str.contains(q_type, na=False, case=False)]
                    if len(type_df) > 0:
                        type_scores = type_df["ragas_relevancy_score"].dropna()
                        if len(type_scores) > 0:
                            type_relevancy[f"{q_type}_relevancy_score"] = float(type_scores.mean())
                            type_relevancy[f"{q_type}_relevancy_score_percentage"] = float(type_scores.mean() * 100)
                            type_relevancy[f"{q_type}_count"] = int(len(type_scores))
                
                if type_relevancy:
                    summary.update(type_relevancy)
    
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
    score_column = None
    if mode == "hybrid" and "hybrid_score" in df.columns:
        score_column = "hybrid_score"
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
    
    # 生成优化建议
    optimization_suggestions = []
    
    # 提示词优化建议
    if "ragas_relevancy_score" in summary:
        relevancy = summary.get("ragas_relevancy_score", 0.0) or 0.0
        if relevancy < 0.7:
            optimization_suggestions.append({
                "category": "提示词优化",
                "metric": "相关性得分",
                "current_value": f"{relevancy:.2%}",
                "suggestion": "相关性得分较低，建议优化提示词工程，提高答案与问题的相关性"
            })
    
    if "ragas_quality_score" in summary:
        quality = summary.get("ragas_quality_score", 0.0) or 0.0
        if quality < 0.7:
            optimization_suggestions.append({
                "category": "提示词优化",
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
    
    # 保存结果
    results_csv_path = output_dir / "evaluation_results.csv"
    df.to_csv(results_csv_path, index=False, encoding="utf-8-sig")
    
    summary_json_path = output_dir / "evaluation_summary.json"
    with open(summary_json_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    
    total_time = time.time() - start_time
    summary["total_time"] = total_time
    
    result = {
        "results_csv_path": str(results_csv_path),
        "summary_json_path": str(summary_json_path),
        "summary": summary,
        "total_questions": total_questions,
        "total_time": total_time,
        "mode": mode,
    }
    
    logger.info(f"评测完成（模式: {mode}）: 耗时 {total_time:.2f}秒")
    if mode in ("chapter_match", "hybrid"):
        chapter_pct = summary.get('chapter_match_accuracy_percentage') or 0
        logger.info(f"  章节匹配准确率: {chapter_pct:.2f}%")
    if mode in ("ragas", "hybrid"):
        ragas_pct = summary.get('ragas_overall_score_percentage') or 0
        logger.info(f"  Ragas 综合得分: {ragas_pct:.2f}%")
    if mode == "hybrid":
        hybrid_pct = summary.get('hybrid_score_percentage') or 0
        logger.info(f"  混合综合得分: {hybrid_pct:.2f}%")
    
    return result
