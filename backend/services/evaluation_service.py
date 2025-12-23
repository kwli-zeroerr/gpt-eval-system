"""
评测服务 - 支持章节匹配和 Ragas AI 评测的混合评测系统
"""
import csv
import logging
import pandas as pd
from pathlib import Path
from typing import List, Dict, Optional, Callable, Awaitable, Literal
from dataclasses import dataclass
import asyncio
import time
import json
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
    }
    
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
