"""
评测服务 - 使用 MetricsCalculator 和 EvaluationRunner 进行评测
从 ragflow-evaluation-tool 集成并适配
"""
import csv
import logging
import pandas as pd
from pathlib import Path
from typing import List, Dict, Optional, Callable, Awaitable
from dataclasses import dataclass
import asyncio
from concurrent.futures import ThreadPoolExecutor
import time
import json
import re

from services.ragflow_client import RagFlowClient, RetrievalConfig
from services.chapter_matcher import ChapterMatcher

logger = logging.getLogger(__name__)


@dataclass
class TestCase:
    """测试用例结构"""
    question: str
    answer: str  # 检索得到的答案（章节信息）
    reference: str  # 标注的章节信息
    type: Optional[str] = None
    theme: Optional[str] = None


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
            test_cases.append(TestCase(
                question=row.get("question", "").strip(),
                answer=row.get("answer", "").strip(),  # 检索得到的答案
                reference=row.get("reference", "").strip(),  # 标注的章节
                type=row.get("type", "").strip() or None,
                theme=row.get("theme", "").strip() or None,
            ))
    
    logger.info(f"从 CSV 加载测试用例用于评测: {len(test_cases)} 条")
    return test_cases


def evaluate_single_case(test_case: TestCase) -> Dict:
    """
    评测单个测试用例
    
    比较检索得到的答案（answer）和标注的章节（reference）
    """
    # 提取章节信息
    answer_chapter = ChapterMatcher.extract_chapter_info(test_case.answer) if test_case.answer else None
    reference_chapter = ChapterMatcher.extract_chapter_info(test_case.reference) if test_case.reference else None
    
    # 计算准确率（二元：是否匹配）
    accuracy = 1.0 if (answer_chapter and reference_chapter and 
                      ChapterMatcher.is_valid_match(answer_chapter, reference_chapter)) else 0.0
    
    # 召回率（这里简化为与准确率相同，因为只有一个答案）
    recall = accuracy
    
    return {
        "question": test_case.question,
        "answer": test_case.answer,
        "reference": test_case.reference,
        "type": test_case.type,
        "theme": test_case.theme,
        "answer_chapter": answer_chapter or "",
        "reference_chapter": reference_chapter or "",
        "accuracy": accuracy,
        "recall": recall,
        "matched": accuracy == 1.0,
    }


async def run_evaluation(
    csv_path: str,
    output_dir: Optional[str] = None,
    progress_callback: Optional[Callable[[int, int, Dict], Awaitable[None]]] = None,
) -> Dict:
    """
    运行评测流程：读取带答案的 CSV，计算指标，生成报告
    
    Args:
        csv_path: 包含答案的 CSV 文件路径
        output_dir: 输出目录（可选）
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
    
    # 设置输出目录 - 保存到 data/evaluation/ 目录
    if output_dir is None:
        evaluation_dir = Path("data/evaluation")
        evaluation_dir.mkdir(parents=True, exist_ok=True)
        csv_path_obj = Path(csv_path)
        output_dir = evaluation_dir / f"{csv_path_obj.stem}_evaluation_results"
    else:
        output_dir = Path(output_dir)
    
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 评测所有用例
    results = []
    for idx, test_case in enumerate(test_cases, 1):
        result = evaluate_single_case(test_case)
        results.append(result)
        
        # 发送进度更新
        if progress_callback:
            await progress_callback(
                idx - 1,
                total,
                {"status": "evaluating", "current": idx, "total": total}
            )
    
    # 转换为 DataFrame
    df = pd.DataFrame(results)
    
    # 计算总体指标
    total_questions = len(results)
    correct_count = sum(1 for r in results if r["matched"])
    accuracy_avg = df["accuracy"].mean() if len(df) > 0 else 0.0
    recall_avg = df["recall"].mean() if len(df) > 0 else 0.0
    
    summary = {
        "total_questions": total_questions,
        "correct_count": correct_count,
        "accuracy": accuracy_avg,
        "recall": recall_avg,
        "accuracy_percentage": accuracy_avg * 100,
        "recall_percentage": recall_avg * 100,
    }
    
    # 保存结果
    results_csv_path = output_dir / "evaluation_results.csv"
    df.to_csv(results_csv_path, index=False, encoding="utf-8-sig")
    
    summary_json_path = output_dir / "evaluation_summary.json"
    with open(summary_json_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    
    total_time = time.time() - start_time
    
    result = {
        "results_csv_path": str(results_csv_path),
        "summary_json_path": str(summary_json_path),
        "summary": summary,
        "total_questions": total_questions,
        "total_time": total_time,
    }
    
    logger.info(f"评测完成: 准确率={accuracy_avg:.4f}, 召回率={recall_avg:.4f}, 耗时 {total_time:.2f}秒")
    
    return result

