"""
检索服务 - 从 CSV 读取问题，调用 RagFlow API 获取答案，填充到 CSV
"""
import csv
import logging
import os
import sys
from pathlib import Path
from typing import List, Dict, Optional, Callable, Awaitable
from dataclasses import dataclass
import asyncio
from concurrent.futures import ThreadPoolExecutor
import time
import threading
from config.paths import DATA_RETRIEVAL_DIR

from services.ragflow_client import RagFlowClient, RetrievalConfig
from services.chapter_matcher import ChapterMatcher

# 增加 CSV 字段大小限制（默认 131072 字节，增加到 10MB）
csv.field_size_limit(min(sys.maxsize, 10 * 1024 * 1024))

logger = logging.getLogger(__name__)


@dataclass
class TestCase:
    """测试用例结构 - 与 CSV 格式对应"""
    question: str
    answer: str  # 初始为空，检索后填充（完整答案）
    answer_chapter: str  # 从完整答案中提取的章节信息（用于传统评测）
    reference: str
    type: Optional[str] = None
    theme: Optional[str] = None
    retrieved_context: str = ""  # 检索到的上下文（用于Ragas评测）


def load_test_cases_from_csv(csv_path: str) -> List[TestCase]:
    """从 CSV 文件加载测试用例"""
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
                reference=row.get("reference", "").strip(),
                type=row.get("type", "").strip() or None,
                theme=row.get("theme", "").strip() or None,
                retrieved_context=retrieved_context,  # 检索上下文
            ))
    
    logger.info(f"加载测试用例: {len(test_cases)} 条")
    return test_cases


def save_test_cases_to_csv(test_cases: List[TestCase], csv_path: str):
    """将测试用例保存到 CSV 文件"""
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        # 更新 CSV 格式：添加 answer_chapter 和 retrieved_context 字段
        writer.writerow(["question", "answer", "answer_chapter", "reference", "type", "theme", "retrieved_context"])
        
        for tc in test_cases:
            writer.writerow([
                tc.question,
                tc.answer,  # 完整答案
                tc.answer_chapter,  # 章节信息
                tc.reference,
                tc.type or "",
                tc.theme or "",
                tc.retrieved_context or "",  # 检索上下文
            ])
    
    logger.debug(f"测试用例已保存到 CSV: {csv_path}")


def assemble_retrieved_context(response: Dict, top_k: int = 3) -> str:
    """
    从 RagFlow 检索响应中组装检索上下文
    
    Args:
        response: RagFlow 检索 API 响应
        top_k: 取前 k 个 chunk 组装上下文
    
    Returns:
        组装后的上下文字符串
    """
    if "error" in response or response.get('code') != 0:
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
    
    # 排序并取前 top_k 个
    sorted_chunks = sorted(chunks, key=lambda c: get_similarity_score(c) or float('-inf'), reverse=True)
    top_chunks = sorted_chunks[:top_k]
    
    # 组装上下文
    context_parts = []
    for chunk in top_chunks:
        content = chunk.get('content', '')
        metadata = chunk.get('metadata', {})
        document_name = metadata.get('document_name', '')
        
        parts = []
        if content:
            parts.append(content)
        if document_name:
            parts.append(f"【来源】{document_name}")
        
        if parts:
            context_parts.append("\n".join(parts))
    
    return "\n---\n".join(context_parts)


def extract_answer_from_response(response: Dict, theme: Optional[str] = None) -> str:
    """
    从 RagFlow API 响应中提取答案（章节信息）
    
    注意：此函数已废弃，现在使用 completion API 生成完整答案
    保留此函数仅用于向后兼容
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
    
    # 优先从 chunk content 中提取章节信息（content 通常包含章节标题）
    content = top_chunk.get('content', '')
    if content:
        # 尝试从 content 开头提取章节号（通常格式为 "## 7.2 标题" 或 "第7章" 等）
        chapter_info = ChapterMatcher.extract_chapter_info(content)
        if chapter_info:
            return chapter_info
    
    # 回退到从 important_keywords 提取章节信息
    important_keywords = top_chunk.get('important_keywords', [])
    if important_keywords and len(important_keywords) >= 2:
        # 尝试从第二个和第三个元素提取章节号
        for keyword_idx in [2, 1]:  # 优先第三个，然后第二个
            if len(important_keywords) > keyword_idx and important_keywords[keyword_idx]:
                keyword = str(important_keywords[keyword_idx])
                # 检查是否包含章节号格式（如 "13.2" 或包含 "第"、"章"、"节"）
                if '.' in keyword or any(c in keyword for c in ['第', '章', '节']):
                    chapter_info = ChapterMatcher.extract_chapter_info(keyword)
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
    
    # 创建检索配置（从传入的配置或环境变量）
    if retrieval_config is None:
        retrieval_config = {}
    
    # 使用 from_dict 方法创建配置，支持所有参数
    config = RetrievalConfig.from_dict(retrieval_config)
    
    # 获取所有数据集 ID（用于创建 chat assistant）
    all_dataset_ids = []
    try:
        api_result = client.list_datasets()
        api_datasets = api_result.get("data", {}).get("data", []) if isinstance(api_result.get("data"), dict) else api_result.get("data", [])
        if not api_datasets and isinstance(api_result, list):
            api_datasets = api_result
        
        for ds in api_datasets:
            ds_id = ds.get("id") or ds.get("dataset_id") or ds.get("_id")
            if ds_id:
                all_dataset_ids.append(ds_id)
    except Exception as e:
        logger.warning(f"获取数据集列表失败: {e}")
    
    # 方案D：混合方案 - 根据问题类型选择对应的 assistant
    # S6 使用专门的 assistant，S1-S5 使用 Normal assistant
    normal_chat_id = None
    s6_chat_id = None
    if all_dataset_ids:
        normal_chat_id = client.find_or_create_normal_assistant(all_dataset_ids)
        s6_chat_id = client.find_or_create_s6_assistant(all_dataset_ids)
        if not normal_chat_id or not s6_chat_id:
            logger.warning("无法创建或找到 chat assistant，将回退到检索模式")
    else:
        logger.warning("没有可用的数据集，将回退到检索模式")
    
    # 为每个 dataset 和 assistant 类型创建 session 的映射
    # 结构：{(dataset_id, assistant_type) -> session_id}
    # assistant_type: "normal" 或 "s6"
    dataset_session_map: Dict[tuple, str] = {}
    session_map_lock = threading.Lock()
    
    # 生成输出 CSV 路径 - 保存到 data/retrieval/ 目录
    csv_path_obj = Path(csv_path)
    retrieval_dir = DATA_RETRIEVAL_DIR
    retrieval_dir.mkdir(parents=True, exist_ok=True)
    output_csv_path = retrieval_dir / f"{csv_path_obj.stem}_with_answers.csv"
    
    # 使用线程安全的计数器
    completed = 0
    failed = 0
    counter_lock = threading.Lock()
    
    def get_or_create_session(dataset_id: str, question_type: Optional[str] = None, max_retries: int = 3) -> Optional[tuple]:
        """
        线程安全地获取或创建 session，带重试机制
        
        Args:
            dataset_id: 数据集ID
            question_type: 问题类型（用于选择 assistant）
            max_retries: 最大重试次数
        
        Returns:
            (chat_id, session_id) 元组，或 None（如果失败）
        """
        # 根据问题类型选择 assistant
        is_s6 = question_type and "S6" in question_type
        assistant_type = "s6" if is_s6 else "normal"
        chat_id = s6_chat_id if is_s6 else normal_chat_id
        
        if not chat_id:
            logger.warning(f"无法获取 {assistant_type} assistant，跳过")
            return None
        
        # 先检查是否已存在
        session_key = (dataset_id, assistant_type)
        with session_map_lock:
            if session_key in dataset_session_map:
                return (chat_id, dataset_session_map[session_key])
        
        # 创建新 session（带重试）
        for attempt in range(max_retries):
            try:
                session_name = f"Session-{dataset_id[:8]}-{assistant_type}"
                session_id = client.create_session(chat_id, session_name)
                
                if session_id:
                    # 线程安全地添加到映射
                    with session_map_lock:
                        # 双重检查，避免并发创建重复的 session
                        if session_key not in dataset_session_map:
                            dataset_session_map[session_key] = session_id
                            logger.debug(f"为 dataset {dataset_id[:8]} ({assistant_type}) 创建 session: {session_id[:8]}")
                        else:
                            # 如果其他线程已经创建，使用已存在的 session
                            session_id = dataset_session_map[session_key]
                    return (chat_id, session_id)
                else:
                    if attempt < max_retries - 1:
                        wait_time = (attempt + 1) * 0.5  # 指数退避
                        logger.warning(f"创建 session 失败（尝试 {attempt + 1}/{max_retries}），{wait_time:.1f}秒后重试...")
                        time.sleep(wait_time)
            except Exception as e:
                if attempt < max_retries - 1:
                    wait_time = (attempt + 1) * 0.5
                    logger.warning(f"创建 session 异常（尝试 {attempt + 1}/{max_retries}）: {e}，{wait_time:.1f}秒后重试...")
                    time.sleep(wait_time)
                else:
                    logger.error(f"创建 session 失败（已重试 {max_retries} 次）: {e}")
        
        return None
    
    def chat_completion_with_retry(chat_id: str, question: str, question_type: Optional[str] = None, max_retries: int = 3) -> Optional[str]:
        """
        带重试机制的 chat_completion
        
        Args:
            chat_id: chat assistant ID
            question: 问题文本
            question_type: 问题类型（已不再需要，因为 S6 的逻辑已内置在 assistant 中）
            max_retries: 最大重试次数
        
        Returns:
            答案文本或 None
        """
        for attempt in range(max_retries):
            try:
                # 注意：S6 的系统提示已内置在 S6 assistant 的 prompt_config 中
                # 所以这里不需要再传递 question_type
                answer = client.chat_completion(
                    chat_id=chat_id,
                    question=question,
                    stream=False,
                    reference=False,
                    question_type=None  # 不再需要，因为已内置在 assistant 配置中
                )
                
                if answer:
                    return answer.strip()
                else:
                    if attempt < max_retries - 1:
                        wait_time = (attempt + 1) * 0.5
                        logger.warning(f"Completion API 返回空答案（尝试 {attempt + 1}/{max_retries}），{wait_time:.1f}秒后重试...")
                        time.sleep(wait_time)
            except Exception as e:
                if attempt < max_retries - 1:
                    wait_time = (attempt + 1) * 0.5
                    logger.warning(f"Completion API 调用异常（尝试 {attempt + 1}/{max_retries}）: {e}，{wait_time:.1f}秒后重试...")
                    time.sleep(wait_time)
                else:
                    logger.error(f"Completion API 调用失败（已重试 {max_retries} 次）: {e}")
        
        return None
    
    def process_single_case(idx: int, test_case: TestCase) -> bool:
        """处理单个测试用例（同步函数，用于线程池）"""
        nonlocal completed, failed
        
        try:
            # 如果答案已存在，跳过
            if test_case.answer:
                logger.debug(f"[{idx}/{total}] 跳过（已有答案）")
                with counter_lock:
                    completed += 1
                return True
            
            # 使用 completion API 生成完整答案
            try:
                # 根据问题类型选择对应的 assistant（方案D：混合方案）
                is_s6 = test_case.type and "S6" in test_case.type
                current_chat_id = s6_chat_id if is_s6 else normal_chat_id
                
                if current_chat_id:
                    # 根据 theme 确定 dataset_id（用于创建 session）
                    dataset_id = None
                    if test_case.theme:
                        theme_datasets = client.get_datasets_by_theme(test_case.theme, datasets_json_path)
                        if theme_datasets:
                            dataset_id = theme_datasets[0].get("id")
                    
                    # 如果没有找到 dataset_id，使用第一个可用的 dataset
                    if not dataset_id and all_dataset_ids:
                        dataset_id = all_dataset_ids[0]
                    
                    # 为每个 dataset 创建或获取 session（线程安全，带重试）
                    if dataset_id:
                        session_result = get_or_create_session(dataset_id, test_case.type)
                        if not session_result:
                            logger.warning(f"[{idx}/{total}] 无法创建或获取 session，跳过")
                            test_case.answer = ""
                            test_case.answer_chapter = ""
                            test_case.retrieved_context = ""
                            with counter_lock:
                                failed += 1
                            return False
                        
                        chat_id_for_question, session_id = session_result
                        
                        # 先调用检索 API 获取上下文（用于后续评测）
                        retrieval_response = client.search(
                            test_case.question,
                            test_case.theme,
                            config,
                            datasets_json_path=datasets_json_path,
                        )
                        
                        # 组装检索上下文
                        if retrieval_response.get('code') == 0:
                            test_case.retrieved_context = assemble_retrieved_context(retrieval_response, top_k=3)
                        else:
                            test_case.retrieved_context = ""
                            logger.debug(f"[{idx}/{total}] 检索 API 返回错误，无法获取上下文")
                        
                        # 使用 completion API 生成完整答案（带重试）
                        answer_text = chat_completion_with_retry(
                            chat_id_for_question,
                            test_case.question,
                            test_case.type
                        )
                        
                        if answer_text:
                            test_case.answer = answer_text
                            # 从完整答案中提取章节信息
                            test_case.answer_chapter = ChapterMatcher.extract_chapter_info(answer_text) or ""
                            
                            # 检查是否是"未找到"的标准回复
                            if "not found" in answer_text.lower() or "找不到" in answer_text or "无法找到" in answer_text:
                                logger.warning(f"[{idx}/{total}] ⚠ AI 返回未找到答案: {answer_text[:80]}")
                            else:
                                logger.info(f"[{idx}/{total}] ✓ {test_case.question[:40]}... -> {answer_text[:60]}...")
                        else:
                            logger.warning(f"[{idx}/{total}] ✗ Completion API 返回空答案（可能是 API 调用失败或 AI 无法生成答案）")
                            test_case.answer = ""
                            test_case.answer_chapter = ""
                    else:
                        logger.warning(f"[{idx}/{total}] 无法确定 dataset_id，跳过")
                        test_case.answer = ""
                        test_case.answer_chapter = ""
                        test_case.retrieved_context = ""
                else:
                    # 回退到检索模式（提取章节号）
                    response = client.search(
                        test_case.question,
                        test_case.theme,
                        config,
                        datasets_json_path=datasets_json_path,
                    )
                    
                    # 检查是否有错误
                    if "error" in response or response.get('code') != 0:
                        error_msg = response.get('error') or response.get('message', 'Unknown error')
                        logger.warning(f"[{idx}/{total}] 检索失败: {error_msg[:60]}")
                        test_case.answer = ""
                        test_case.answer_chapter = ""
                        test_case.retrieved_context = ""
                    else:
                        # 组装检索上下文
                        test_case.retrieved_context = assemble_retrieved_context(response, top_k=3)
                        
                        # 提取答案（章节信息）
                        answer_chapter = extract_answer_from_response(response, test_case.theme)
                        test_case.answer_chapter = answer_chapter
                        # 检索模式没有完整答案，只保留章节信息
                        test_case.answer = answer_chapter
                        
                        data = response.get('data', {})
                        chunks = data.get('chunks', []) if isinstance(data, dict) else []
                        
                        if answer_chapter:
                            logger.info(f"[{idx}/{total}] ✓ {test_case.question[:40]}... -> {answer_chapter}")
                        else:
                            if len(chunks) == 0:
                                logger.warning(f"[{idx}/{total}] ✗ 未找到答案：检索 API 未返回任何 chunk（可能是相似度阈值过高或知识库中没有相关内容）")
                            else:
                                logger.warning(f"[{idx}/{total}] ✗ 未找到答案：检索 API 返回了 {len(chunks)} 个 chunk，但无法提取章节信息（可能是章节匹配失败）")
            except Exception as api_error:
                logger.error(f"[{idx}/{total}] 检索异常: {str(api_error)[:80]}", exc_info=True)
                test_case.answer = ""
                test_case.answer_chapter = ""
                test_case.retrieved_context = ""
                raise  # 重新抛出异常，让外层捕获
            
            with counter_lock:
                completed += 1
            return True
            
        except Exception as e:
            logger.error(f"[检索 {idx}/{total}] 失败: {test_case.question[:50]}... - {str(e)}", exc_info=True)
            test_case.answer = ""
            test_case.answer_chapter = ""
            test_case.retrieved_context = ""
            with counter_lock:
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
        logger.debug("使用顺序模式检索")
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
    
    # 清理：删除所有创建的 sessions（无痕操作）
    # 方案D：清理两个 assistant 的 sessions
    total_deleted = 0
    total_sessions = 0
    
    for chat_id_to_clean, assistant_name in [(normal_chat_id, "Normal"), (s6_chat_id, "S6")]:
        if chat_id_to_clean:
            # 收集该 assistant 的所有 sessions
            with session_map_lock:
                session_ids = [
                    session_id for (dataset_id, assistant_type), session_id in dataset_session_map.items()
                    if assistant_type == ("s6" if chat_id_to_clean == s6_chat_id else "normal")
                ]
            
            if session_ids:
                total_sessions += len(session_ids)
                try:
                    deleted_count = client.delete_sessions(chat_id_to_clean, session_ids)
                    total_deleted += deleted_count
                    if deleted_count == len(session_ids):
                        logger.info(f"已清理 {assistant_name} assistant 的 {deleted_count}/{len(session_ids)} 个 sessions")
                    else:
                        logger.warning(f"部分清理 {assistant_name} assistant sessions: {deleted_count}/{len(session_ids)} 成功")
                except Exception as e:
                    logger.warning(f"清理 {assistant_name} assistant sessions 失败: {e}")
    
    if total_sessions > 0:
        logger.info(f"总共清理 {total_deleted}/{total_sessions} 个 sessions（无痕模式）")
    
    result = {
        "output_csv_path": str(output_csv_path),
        "input_csv_path": csv_path,
        "total_questions": total,
        "completed": completed,
        "failed": failed,
        "total_time": total_time,
    }
    
    logger.info(f"检索完成: {completed}/{total} 成功, {failed} 失败 ({total_time:.1f}s)")
    
    return result

