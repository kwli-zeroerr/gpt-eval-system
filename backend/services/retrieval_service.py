"""
检索服务 - 从 CSV 读取问题，调用 RagFlow API 获取答案，填充到 CSV
"""
import csv
import logging
import os
from pathlib import Path
from typing import List, Dict, Optional, Callable, Awaitable
from dataclasses import dataclass
import asyncio
from concurrent.futures import ThreadPoolExecutor
import time

from services.ragflow_client import RagFlowClient, RetrievalConfig
from services.chapter_matcher import ChapterMatcher

logger = logging.getLogger(__name__)


@dataclass
class TestCase:
    """测试用例结构 - 与 CSV 格式对应"""
    question: str
    answer: str  # 初始为空，检索后填充
    reference: str
    type: Optional[str] = None
    theme: Optional[str] = None


def load_test_cases_from_csv(csv_path: str) -> List[TestCase]:
    """从 CSV 文件加载测试用例"""
    test_cases = []
    
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            test_cases.append(TestCase(
                question=row.get("question", "").strip(),
                answer=row.get("answer", "").strip(),  # 可能已有值，也可能为空
                reference=row.get("reference", "").strip(),
                type=row.get("type", "").strip() or None,
                theme=row.get("theme", "").strip() or None,
            ))
    
    logger.info(f"从 CSV 加载测试用例: {len(test_cases)} 条")
    return test_cases


def save_test_cases_to_csv(test_cases: List[TestCase], csv_path: str):
    """将测试用例保存到 CSV 文件"""
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["question", "answer", "reference", "type", "theme"])
        
        for tc in test_cases:
            writer.writerow([
                tc.question,
                tc.answer,
                tc.reference,
                tc.type or "",
                tc.theme or "",
            ])
    
    logger.info(f"测试用例已保存到 CSV: {csv_path}")


def extract_answer_from_response(response: Dict, theme: Optional[str] = None) -> str:
    """
    从 RagFlow API 响应中提取答案（章节信息）
    
    返回第一个结果的章节信息作为答案
    """
    if "error" in response:
        return ""
    
    data = response.get('data', {})
    chunks = data.get('chunks', []) if isinstance(data, dict) else []
    
    if not chunks:
        return ""
    
    # 基于相似度排序
    def get_similarity_score(chunk: Dict) -> Optional[float]:
        if isinstance(chunk, dict):
            if isinstance(chunk.get('similarity'), (int, float)):
                return float(chunk['similarity'])
            if isinstance(chunk.get('score'), (int, float)):
                return float(chunk['score'])
            if isinstance(chunk.get('relevance'), (int, float)):
                return float(chunk['relevance'])
            if isinstance(chunk.get('distance'), (int, float)):
                return -float(chunk['distance'])
        return None
    
    # 排序并取第一个
    sorted_chunks = sorted(chunks, key=lambda c: get_similarity_score(c) or float('-inf'), reverse=True)
    top_chunk = sorted_chunks[0] if sorted_chunks else None
    
    if not top_chunk:
        return ""
    
    # 从 important_keywords 提取章节信息
    important_keywords = top_chunk.get('important_keywords', [])
    if not important_keywords or len(important_keywords) < 2:
        return ""
    
    # 优先提取小章（第三个元素），没有则提取大章（第二个元素）
    if len(important_keywords) >= 3 and important_keywords[2]:
        chapter_info = ChapterMatcher.extract_chapter_info(str(important_keywords[2]))
        if chapter_info:
            return chapter_info
    
    if len(important_keywords) >= 2:
        chapter_info = ChapterMatcher.extract_chapter_info(str(important_keywords[1]))
        if chapter_info:
            return chapter_info
    
    return ""


async def run_retrieval(
    csv_path: str,
    ragflow_api_url: str,
    ragflow_api_key: str,
    retrieval_config: Optional[Dict] = None,
    datasets_json_path: Optional[str] = None,
    max_workers: int = 1,
    delay_between_requests: float = 0.5,
    progress_callback: Optional[Callable[[int, int, Dict], Awaitable[None]]] = None,
) -> Dict:
    """
    运行检索流程：读取 CSV，调用 RagFlow API，填充答案
    
    Args:
        csv_path: CSV 文件路径
        ragflow_api_url: RagFlow API 地址
        ragflow_api_key: RagFlow API 密钥
        retrieval_config: 检索配置参数（可选）
        datasets_json_path: datasets.json 文件路径（可选）
        max_workers: 并发线程数
        delay_between_requests: 请求延迟（秒）
        progress_callback: 进度回调函数 (current, total, data)
    
    Returns:
        Dict with results: {output_csv_path, total_questions, completed, failed, total_time}
    """
    start_time = time.time()
    
    # 加载测试用例
    test_cases = load_test_cases_from_csv(csv_path)
    total = len(test_cases)
    
    if total == 0:
        raise ValueError("CSV 文件中没有测试用例")
    
    # 创建 RagFlow 客户端
    client = RagFlowClient(ragflow_api_url, ragflow_api_key)
    
    # 创建检索配置
    if retrieval_config is None:
        retrieval_config = {}
    
    config = RetrievalConfig(
        dataset_ids=retrieval_config.get("dataset_ids", []),
        top_k=retrieval_config.get("top_k", 5),
        similarity_threshold=retrieval_config.get("similarity_threshold", 0.0),
        vector_similarity_weight=retrieval_config.get("vector_similarity_weight"),
        document_ids=retrieval_config.get("document_ids"),
        page=retrieval_config.get("page"),
        page_size=retrieval_config.get("page_size"),
        rerank_id=retrieval_config.get("rerank_id"),
        keyword=retrieval_config.get("keyword"),
        highlight=retrieval_config.get("highlight"),
    )
    
    # 生成输出 CSV 路径 - 保存到 data/retrieval/ 目录
    csv_path_obj = Path(csv_path)
    retrieval_dir = Path("data/retrieval")
    retrieval_dir.mkdir(parents=True, exist_ok=True)
    output_csv_path = retrieval_dir / f"{csv_path_obj.stem}_with_answers.csv"
    
    completed = 0
    failed = 0
    
    def process_single_case(idx: int, test_case: TestCase) -> bool:
        """处理单个测试用例（同步函数，用于线程池）"""
        nonlocal completed, failed
        
        try:
            # 如果答案已存在，跳过
            if test_case.answer:
                logger.info(f"[检索 {idx}/{total}] 问题已有答案，跳过: {test_case.question[:50]}...")
                completed += 1
                return True
            
            # 调用 RagFlow API
            response = client.search(
                test_case.question,
                test_case.theme,
                config,
                datasets_json_path=datasets_json_path,
            )
            
            # 提取答案
            answer = extract_answer_from_response(response, test_case.theme)
            test_case.answer = answer
            
            if answer:
                logger.info(f"[检索 {idx}/{total}] 成功: {test_case.question[:50]}... -> {answer}")
            else:
                logger.warning(f"[检索 {idx}/{total}] 未找到答案: {test_case.question[:50]}...")
            
            completed += 1
            return True
            
        except Exception as e:
            logger.error(f"[检索 {idx}/{total}] 失败: {test_case.question[:50]}... - {str(e)}")
            failed += 1
            return False
    
    # 执行检索（支持并发）
    if max_workers > 1:
        logger.info(f"使用并发模式检索，并发数: {max_workers}")
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = []
            for idx, test_case in enumerate(test_cases, 1):
                future = executor.submit(process_single_case, idx, test_case)
                futures.append((idx, future))
                
                # 发送进度更新
                if progress_callback:
                    asyncio.create_task(progress_callback(
                        idx - 1,
                        total,
                        {"status": "processing", "current": idx, "total": total}
                    ))
                
                # 延迟以避免限流
                if delay_between_requests > 0:
                    time.sleep(delay_between_requests)
            
            # 等待所有任务完成
            for idx, future in futures:
                try:
                    future.result()
                except Exception as e:
                    logger.error(f"任务 {idx} 执行异常: {str(e)}")
    else:
        logger.info("使用顺序模式检索")
        for idx, test_case in enumerate(test_cases, 1):
            process_single_case(idx, test_case)
            
            # 发送进度更新
            if progress_callback:
                await progress_callback(
                    idx - 1,
                    total,
                    {"status": "processing", "current": idx, "total": total}
                )
            
            # 延迟以避免限流
            if delay_between_requests > 0:
                await asyncio.sleep(delay_between_requests)
    
    # 保存结果到 CSV
    save_test_cases_to_csv(test_cases, str(output_csv_path))
    
    total_time = time.time() - start_time
    
    result = {
        "output_csv_path": str(output_csv_path),
        "input_csv_path": csv_path,
        "total_questions": total,
        "completed": completed,
        "failed": failed,
        "total_time": total_time,
    }
    
    logger.info(f"检索完成: {completed}/{total} 成功, {failed} 失败, 耗时 {total_time:.2f}秒")
    
    return result

