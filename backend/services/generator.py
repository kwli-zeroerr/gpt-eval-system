import uuid
import logging
import time
import asyncio
import re
import os
import multiprocessing
import threading
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Dict, List, Optional, Callable, Awaitable, Tuple
from difflib import SequenceMatcher
try:
    from tqdm.asyncio import tqdm
except ImportError:
    # Fallback for environments without tqdm
    class tqdm:
        def __init__(self, *args, **kwargs):
            self.total = kwargs.get('total', 0)
            self.desc = ""
        def set_description(self, desc):
            self.desc = desc
        def update(self, n=1):
            pass
        def close(self):
            pass

from schemas import QuestionItem
from .llm_client import call_llm, call_llm_sync
from .templates import get_category_dict, make_prompt
from .minio_client import MinIOClient
from services.chapter_matcher import ChapterMatcher

logger = logging.getLogger(__name__)

# Maximum number of processes for multiprocessing
# 降低默认并发数以避免触发 rate limit
# 默认值：min(4, cpu_count)，但至少为 2
cpu_count = multiprocessing.cpu_count() or 4
MAX_PROCESSES = int(os.getenv('MAX_PROCESSES', min(4, cpu_count) if cpu_count > 4 else max(2, cpu_count)))


async def _generate_for_category(cat_id: str, prompt: str, count: int, reference: str = "") -> List[QuestionItem]:
    """Generate questions for a category, parsing JSON response from LLM."""
    import json
    import re
    
    raw = await call_llm(prompt, n=count)
    questions = []
    
    # Try to parse as JSON first
    try:
        # Extract JSON from markdown code blocks if present
        json_match = re.search(r'```(?:json)?\s*(\[.*?\]|\{.*?\})\s*```', raw, re.DOTALL)
        if json_match:
            raw = json_match.group(1)
        else:
            # Try to find JSON array or object in the text
            json_match = re.search(r'(\[.*?\]|\{.*?\})', raw, re.DOTALL)
            if json_match:
                raw = json_match.group(1)
        
        data = json.loads(raw, strict=False)
        
        # Handle array of objects (most common case)
        if isinstance(data, list):
            # S2 可能返回空数组 []，这是正常的，应该接受
            if len(data) == 0 and cat_id == "S2":
                logger.info(f"S2 类别：文本中没有专有名词定义，返回空数组（这是正常的）")
                return []  # 直接返回空列表，不进行 fallback
            for item in data:
                if isinstance(item, dict):
                    q_text = item.get("question", "")
                    if q_text and q_text.strip():
                        questions.append(QuestionItem(category=cat_id, text=q_text.strip(), reference=reference))
        # Handle single object
        elif isinstance(data, dict):
            q_text = data.get("question", "")
            if q_text and q_text.strip():
                questions.append(QuestionItem(category=cat_id, text=q_text.strip(), reference=reference))
    except (json.JSONDecodeError, AttributeError, ValueError) as e:
        logger.warning(f"解析 {cat_id} 类别的 JSON 响应失败: {e}")
        # Fallback: simple line-based parsing
        lines = [ln.strip("- ").strip() for ln in raw.splitlines() if ln.strip() and not ln.strip().startswith("{") and not ln.strip().startswith("[")]
        # Filter out JSON-like lines
        lines = [ln for ln in lines if not (ln.startswith("{") or ln.startswith("["))]
        lines = lines[:count]
        questions = [QuestionItem(category=cat_id, text=line, reference=reference) for line in lines if line]
    
    # For S2, if no questions found, it's acceptable (no specialized terms in text)
    # For other categories, try fallback only if parsing completely failed
    if not questions and cat_id != "S2":
        # Last resort: split by sentences or newlines
        parts = re.split(r'[.!?]\s+', raw)
        parts = [p.strip() for p in parts if p.strip() and len(p.strip()) > 10][:count]
        questions = [QuestionItem(category=cat_id, text=p, reference=reference) for p in parts]
    
    return questions[:count]  # Limit to requested count


def _generate_for_category_sync(cat_id: str, prompt: str, count: int, reference: str = "") -> List[QuestionItem]:
    """Synchronous version of _generate_for_category for use with ProcessPoolExecutor.
    
    This function must be pickleable (no closures, no lambda functions).
    """
    import json
    import re
    
    # Use synchronous LLM client
    raw = call_llm_sync(prompt, n=count)
    questions = []
    
    # Try to parse as JSON first
    try:
        # Extract JSON from markdown code blocks if present
        json_match = re.search(r'```(?:json)?\s*(\[.*?\]|\{.*?\})\s*```', raw, re.DOTALL)
        if json_match:
            raw = json_match.group(1)
        else:
            # Try to find JSON array or object in the text
            json_match = re.search(r'(\[.*?\]|\{.*?\})', raw, re.DOTALL)
            if json_match:
                raw = json_match.group(1)
        
        data = json.loads(raw, strict=False)
        
        # Handle array of objects (most common case)
        if isinstance(data, list):
            # S2 可能返回空数组 []，这是正常的，应该接受
            if len(data) == 0 and cat_id == "S2":
                return []  # 直接返回空列表，不进行 fallback
            for item in data:
                if isinstance(item, dict):
                    q_text = item.get("question", "")
                    if q_text and q_text.strip():
                        questions.append(QuestionItem(category=cat_id, text=q_text.strip(), reference=reference))
        # Handle single object
        elif isinstance(data, dict):
            q_text = data.get("question", "")
            if q_text and q_text.strip():
                questions.append(QuestionItem(category=cat_id, text=q_text.strip(), reference=reference))
    except (json.JSONDecodeError, AttributeError, ValueError) as e:
        # Fallback: simple line-based parsing
        lines = [ln.strip("- ").strip() for ln in raw.splitlines() if ln.strip() and not ln.strip().startswith("{") and not ln.strip().startswith("[")]
        # Filter out JSON-like lines
        lines = [ln for ln in lines if not (ln.startswith("{") or ln.startswith("["))]
        lines = lines[:count]
        questions = [QuestionItem(category=cat_id, text=line, reference=reference) for line in lines if line]
    
    # For S2, if no questions found, it's acceptable (no specialized terms in text)
    # For other categories, try fallback only if parsing completely failed
    if not questions and cat_id != "S2":
        # Last resort: split by sentences or newlines
        parts = re.split(r'[.!?]\s+', raw)
        parts = [p.strip() for p in parts if p.strip() and len(p.strip()) > 10][:count]
        questions = [QuestionItem(category=cat_id, text=p, reference=reference) for p in parts]
    
    return questions[:count]  # Limit to requested count


def _normalize_text(text: str) -> str:
    """Normalize text for comparison: remove spaces, punctuation, convert to lowercase."""
    # Remove punctuation and spaces, convert to lowercase
    normalized = re.sub(r'[^\w]', '', text.lower())
    return normalized


def _text_similarity(text1: str, text2: str) -> float:
    """Calculate similarity between two texts using SequenceMatcher."""
    norm1 = _normalize_text(text1)
    norm2 = _normalize_text(text2)
    
    # If normalized texts are identical, return 1.0
    if norm1 == norm2:
        return 1.0
    
    # Use SequenceMatcher for similarity
    return SequenceMatcher(None, norm1, norm2).ratio()


def _deduplicate_questions(questions: List[QuestionItem], similarity_threshold: float = 0.9) -> List[QuestionItem]:
    """Remove duplicate questions based on text similarity.
    
    Args:
        questions: List of QuestionItem objects
        similarity_threshold: Similarity threshold (0.0-1.0), questions above this are considered duplicates
    
    Returns:
        List of deduplicated QuestionItem objects (first occurrence kept)
    """
    if not questions:
        return questions
    
    deduplicated = []
    seen_texts = []  # Keep track of normalized texts we've seen
    
    for question in questions:
        is_duplicate = False
        question_text = question.text.strip()
        
        # Check against all previously seen questions
        for seen_text in seen_texts:
            similarity = _text_similarity(question_text, seen_text)
            if similarity >= similarity_threshold:
                is_duplicate = True
                logger.debug(f"Duplicate detected (similarity: {similarity:.2f}): '{question_text[:50]}...' vs '{seen_text[:50]}...'")
                break
        
        if not is_duplicate:
            deduplicated.append(question)
            seen_texts.append(_normalize_text(question_text))
    
    removed_count = len(questions) - len(deduplicated)
    if removed_count > 0:
        logger.info(f"Deduplication removed {removed_count} duplicate questions ({removed_count/len(questions)*100:.1f}%)")
    
    return deduplicated


def _generate_single_question_worker(args):
    """Worker function for ProcessPoolExecutor to generate a single question.
    
    Args:
        args: tuple of (cat_id, prompt, reference, question_index)
    
    Returns:
        tuple of (question_index, questions_list)
    """
    cat_id, prompt, reference, question_index = args
    # 对于 S2 类别，减少重试次数以避免长时间阻塞
    # S2 可能返回空结果，这是正常的，不需要重试
    max_retries = 1 if cat_id == "S2" else 2  # S2 只重试 1 次，其他类别重试 2 次
    retry_delay = 0.5  # 减少重试延迟到 0.5 秒，加快失败恢复
    # Rate limit 需要更长的等待时间
    rate_limit_base_delay = 5.0  # Rate limit 基础延迟 5 秒
    
    for attempt in range(max_retries + 1):
        try:
            questions = _generate_for_category_sync(cat_id, prompt, count=1, reference=reference)
            # 对于 S2，空结果也是可以接受的，直接返回
            if cat_id == "S2" and not questions:
                logger.debug(f"S2 question {question_index} returned empty (no definitions found), skipping retry")
            return (question_index, questions)
        except Exception as e:
            error_msg = str(e)
            is_timeout = "timeout" in error_msg.lower() or "timed out" in error_msg.lower()
            is_rate_limit = "rate limit" in error_msg.lower() or "429" in error_msg or "404" in error_msg
            
            if attempt < max_retries:
                if is_rate_limit:
                    # Rate limit 错误，使用更长的延迟和指数退避
                    delay = rate_limit_base_delay * (2 ** attempt)  # 5s, 10s, 20s...
                    logger.warning(f"Rate limit for question {question_index} for {cat_id} (attempt {attempt + 1}/{max_retries + 1}), waiting {delay:.1f}s before retry...")
                    time.sleep(delay)
                    continue
                elif is_timeout:
                    # 超时错误，等待后重试
                    logger.warning(f"Timeout generating question {question_index} for {cat_id} (attempt {attempt + 1}/{max_retries + 1}), retrying...")
                    time.sleep(retry_delay * (attempt + 1))  # 指数退避
                    continue
            else:
                # 达到最大重试次数
                if is_rate_limit:
                    logger.warning(f"Rate limit for question {question_index} for {cat_id} after {max_retries + 1} attempts, skipping")
                elif cat_id == "S2" and is_timeout:
                    logger.warning(f"S2 question {question_index} timed out after {max_retries + 1} attempts, skipping")
                else:
                    logger.error(f"Error generating question {question_index} for {cat_id}: {e}")
                return (question_index, [])
    
    # 如果所有重试都失败
    return (question_index, [])


def _process_category_worker(args):
    """Worker function to process a single category concurrently.
    
    This function processes all questions for one category using ProcessPoolExecutor.
    Must be pickleable for multiprocessing.
    
    Args:
        args: tuple of (category_id, category_tasks, max_processes, per_category)
            - category_id: str, category ID (e.g., "S1")
            - category_tasks: list of (cat_id, prompt, reference, question_index)
            - max_processes: int, max workers for ProcessPoolExecutor
            - per_category: int, target number of questions per category
    
    Returns:
        tuple of (category_id, category_questions, elapsed_time)
    """
    category_id, category_tasks, max_processes, per_category = args
    cat_start_time = time.time()
    category_questions = []
    category_results = {}  # Map from question_index to questions
    
    try:
        # Use ProcessPoolExecutor to process tasks concurrently
        # 进一步限制每个类别内的并发数，避免过多请求
        # 每个类别最多使用 min(max_processes, 3) 个进程
        per_category_max_workers = min(max_processes, int(os.getenv('PER_CATEGORY_MAX_WORKERS', '3')))
        with ProcessPoolExecutor(max_workers=per_category_max_workers) as executor:
            # Submit all tasks
            futures = {executor.submit(_generate_single_question_worker, task): task[3] 
                      for task in category_tasks}
            
            # Collect results as they complete
            completed_count = 0
            unique_indices = set()
            questions_collected = 0  # Track total questions collected for S2 early stopping
            
            for future in as_completed(futures):
                question_index = futures[future]
                try:
                    idx, questions = future.result()
                    if questions:
                        if idx not in category_results:
                            category_results[idx] = []
                        category_results[idx].extend(questions)
                        unique_indices.add(idx)
                        completed_count += 1
                        questions_collected += len(questions)
                        
                        # For S2, stop when we have enough questions
                        # Use questions_collected for more accurate early stopping
                        if category_id == "S2" and questions_collected >= per_category:
                            # Cancel remaining futures to speed up
                            cancelled_count = 0
                            for f in futures:
                                if not f.done():
                                    f.cancel()
                                    cancelled_count += 1
                            if cancelled_count > 0:
                                logger.info(f"S2: Collected {questions_collected} questions, cancelling {cancelled_count} remaining tasks")
                            break
                except Exception as e:
                    logger.error(f"Error processing task {question_index} for {category_id}: {e}")
            
            # Sort results by question_index and collect questions
            for idx in sorted(category_results.keys()):
                category_questions.extend(category_results[idx])
                # For S2, limit to per_category
                if category_id == "S2":
                    if len(category_questions) >= per_category:
                        category_questions = category_questions[:per_category]
                        break
                elif len(category_questions) >= per_category:
                    category_questions = category_questions[:per_category]
                    break
        
        cat_elapsed = time.time() - cat_start_time
        logger.info(f"Completed {category_id}: {len(category_questions)} questions generated in {cat_elapsed:.2f}s")
        return (category_id, category_questions, cat_elapsed)
    except Exception as e:
        logger.error(f"Error processing category {category_id}: {e}")
        cat_elapsed = time.time() - cat_start_time
        return (category_id, [], cat_elapsed)


async def generate_questions(
    categories: List[str],
    per_category: int,
    prompt_overrides: Dict[str, str],
    docs: Optional[List[str]] = None,
    progress_callback: Optional[Callable] = None,  # Signature: (cat: str, current: int, total: int, elapsed: float | None, active_categories: List[str] | None) -> Awaitable[None]
    category_complete_callback: Optional[Callable[[str, List[QuestionItem], float], Awaitable[None]]] = None,
) -> Tuple[str, List[QuestionItem], Dict[str, float], float]:
    """Generate questions and return (request_id, questions, category_times, total_time)."""
    req_id = str(uuid.uuid4())
    cat_dict = get_category_dict()
    start_time = time.time()
    category_times: Dict[str, float] = {}
    
    # Get context from MinIO knowledge bucket via S3-compatible API
    # Get enough chunks for all questions (categories * per_category, with some buffer)
    minio_client = MinIOClient()
    total_questions = len(categories) * per_category
    # Get more chunks to ensure diversity (at least 2x the number of questions)
    needed_chunks = max(total_questions * 2, 20)  # At least 20 chunks for variety
    
    logger.info(f"Fetching {needed_chunks} chunks from MinIO knowledge bucket...")
    if docs:
        # If specific files provided, use them
        all_chunks_with_refs = minio_client.get_file_chunks(object_names=docs, max_chunks=needed_chunks)
    else:
        # Otherwise, get chunks from knowledge bucket
        all_chunks_with_refs = minio_client.get_file_chunks(max_chunks=needed_chunks)
    
    if not all_chunks_with_refs:
        raise RuntimeError("No content found in MinIO knowledge bucket. Please ensure the bucket has files.")
    
    logger.info(f"Retrieved {len(all_chunks_with_refs)} chunks from MinIO")

    # Prepare all category tasks for cross-category concurrent processing
    all_category_tasks = []  # List of (category_id, category_tasks_list)
    chunk_indices = {cid: 0 for cid in categories}  # Track chunk index per category
    
    # Prepare tasks for all categories (without progress bar during preparation)
    # Progress bar will be created after tasks are prepared
    for cat_idx, cid in enumerate(categories):
        hint = prompt_overrides.get(cid) or (cat_dict.get(cid).default_prompt if cid in cat_dict else "")
        logger.info(f"Preparing tasks for {cid}...")
        
        category_tasks = []
        category_start_index = cat_idx * per_category
        
        for i in range(per_category):
            task_index = category_start_index + i
            chunk_index = chunk_indices[cid]
            
            # For S2, create multiple tasks (one per chunk) to search for definitions
            if cid == "S2":
                # S2: Create tasks for chunks (we'll process them concurrently and stop when we have enough)
                # Optimized: Reduce attempts and remove combo tasks for faster generation
                # Use a multiplier based on per_category to ensure we have enough attempts
                # But cap at a reasonable limit to avoid too many tasks
                max_s2_attempts = min(len(all_chunks_with_refs), max(per_category * 3, 15))
                # Use different starting points for each question to increase diversity
                start_offset = (i * max_s2_attempts // per_category) % len(all_chunks_with_refs)
                for attempt in range(max_s2_attempts):
                    chunk_idx = (start_offset + attempt) % len(all_chunks_with_refs)
                    chunk_text, raw_ref = all_chunks_with_refs[chunk_idx]
                    prompt = make_prompt(
                        category_id=cid,
                        category_hint=hint,
                        count=1,
                        context_snippets=[chunk_text],
                    )
                    # Use unique task_index for each attempt to enable proper early stopping
                    category_tasks.append((cid, prompt, raw_ref, task_index * 1000 + attempt))
            else:
                # For other categories, prepare one task per question
                if chunk_index >= len(all_chunks_with_refs):
                    chunk_index = 0  # Wrap around if we run out of chunks
                
                # For S4/S5, we need 3 chunks; for others, 1 chunk
                if cid in ("S4", "S5"):
                    # Get 3 consecutive chunks with references
                    chunk_refs = all_chunks_with_refs[chunk_index:chunk_index+3]
                    if len(chunk_refs) < 3:
                        # If not enough chunks, pad or wrap around
                        chunk_refs = chunk_refs + all_chunks_with_refs[:3-len(chunk_refs)]
                    context_snippets = [chunk for chunk, _ in chunk_refs]
                    # 多段题型：将所有相关 chunk 的 reference 全部纳入（不省略）
                    refs = [ref for _, ref in chunk_refs if ref]
                    raw_ref = ";".join(refs)
                    chunk_indices[cid] = (chunk_index + 3) % len(all_chunks_with_refs)
                else:
                    # Use single chunk with reference
                    chunk_text, raw_ref = all_chunks_with_refs[chunk_index]
                    context_snippets = [chunk_text]
                    chunk_indices[cid] = (chunk_index + 1) % len(all_chunks_with_refs)
                
                # reference 直接继承自 MinIO chunk
                reference = raw_ref if cid != "S6" else ""
                
                # Build prompt with specific context
                prompt = make_prompt(
                    category_id=cid,
                    category_hint=hint,
                    count=1,
                    context_snippets=context_snippets,
                )
                
                category_tasks.append((cid, prompt, reference, task_index))
        
        all_category_tasks.append((cid, category_tasks, per_category))
        logger.info(f"Prepared {len(category_tasks)} tasks for {cid}")
    
    # Create progress bar after all tasks are prepared
    pbar = tqdm(total=total_questions, desc="并发生成问题", unit="个")
    
    # Process all categories concurrently using ProcessPoolExecutor
    all_questions = []
    current_progress = 0
    progress_lock = threading.Lock()  # Thread-safe progress tracking
    
    loop = asyncio.get_event_loop()
    
    # Use ProcessPoolExecutor for cross-category concurrency
    # 降低类别并发数以避免触发 rate limit
    # 默认最多 3 个类别同时处理（而不是 6 个）
    max_category_workers = min(len(categories), int(os.getenv('MAX_CATEGORY_WORKERS', '3')))
    
    try:
        with ProcessPoolExecutor(max_workers=max_category_workers) as category_executor:
            # Submit all category tasks
            category_futures = {
                category_executor.submit(_process_category_worker, (cid, tasks, MAX_PROCESSES, per_cat)): cid
                for cid, tasks, per_cat in all_category_tasks
            }
            
            # Track category results and active categories
            category_results_map = {}
            active_categories = set(categories)  # Track which categories are currently being processed
            
            # Collect results as categories complete
            for future in as_completed(category_futures):
                cid = category_futures[future]
                try:
                    cat_id, category_questions, cat_elapsed = future.result()
                    category_times[cat_id] = cat_elapsed
                    category_results_map[cat_id] = category_questions
                    active_categories.discard(cat_id)  # Remove from active when completed
                    
                    # Update progress
                    with progress_lock:
                        current_progress += len(category_questions)
                        pbar.update(len(category_questions))
                    
                    # Call category complete callback if provided
                    if category_complete_callback:
                        try:
                            if asyncio.iscoroutinefunction(category_complete_callback):
                                await category_complete_callback(cat_id, category_questions, cat_elapsed)
                            else:
                                await loop.run_in_executor(None,
                                    lambda: category_complete_callback(cat_id, category_questions, cat_elapsed))
                        except Exception as e:
                            logger.warning(f"Category complete callback error: {e}")
                    
                    # Update progress callback with active categories for concurrent display
                    if progress_callback:
                        try:
                            elapsed = time.time() - start_time
                            # Send active categories list for concurrent progress display
                            active_list = list(active_categories)
                            if asyncio.iscoroutinefunction(progress_callback):
                                # Try new signature with active_categories, fallback to old signature
                                try:
                                    await progress_callback(cat_id, current_progress, total_questions, elapsed, active_list)
                                except TypeError:
                                    # Fallback for callbacks that don't support active_categories parameter
                                    await progress_callback(cat_id, current_progress, total_questions, elapsed)
                            else:
                                # For sync callbacks, wrap in executor
                                def call_progress():
                                    try:
                                        progress_callback(cat_id, current_progress, total_questions, elapsed, active_list)
                                    except TypeError:
                                        # Fallback for callbacks that don't support active_categories parameter
                                        progress_callback(cat_id, current_progress, total_questions, elapsed)
                                await loop.run_in_executor(None, call_progress)
                        except Exception as e:
                            logger.warning(f"Progress callback error: {e}")
                            
                except Exception as e:
                    logger.error(f"Error processing category {cid}: {e}")
                    category_times[cid] = 0.0
                    category_results_map[cid] = []
                    active_categories.discard(cid)
            
            # Collect all questions in category order
            for cid in categories:
                if cid in category_results_map:
                    category_questions = category_results_map[cid]
                    # Limit to per_category for each category
                    if len(category_questions) > per_category:
                        category_questions = category_questions[:per_category]
                    all_questions.extend(category_questions)
                    logger.info(f"Collected {len(category_questions)} questions for {cid}")
    
    finally:
        pbar.close()
    
    # Deduplicate questions: first within categories, then globally
    logger.info("Starting deduplication process...")
    original_count = len(all_questions)
    
    # Per-category deduplication
    category_deduplicated = {}
    temp_all_questions = []
    for cid in categories:
        # Extract questions for this category from all_questions
        category_questions = [q for q in all_questions if q.category == cid]
        if category_questions:
            category_deduplicated[cid] = _deduplicate_questions(category_questions, similarity_threshold=0.9)
            # If deduplication removed too many questions, log a warning
            if len(category_deduplicated[cid]) < per_category:
                logger.warning(
                    f"Category {cid}: After deduplication, only {len(category_deduplicated[cid])} questions remain "
                    f"(expected {per_category}). Original: {len(category_questions)}"
                )
            temp_all_questions.extend(category_deduplicated[cid])
            logger.info(f"Deduplicated {cid}: {len(category_questions)} -> {len(category_deduplicated[cid])} questions")
    
    # Final global deduplication across all categories
    all_questions = _deduplicate_questions(temp_all_questions, similarity_threshold=0.9)
    
    total_time = time.time() - start_time
    logger.info(f"Total questions generated: {len(all_questions)} (removed {original_count - len(all_questions)} duplicates) in {total_time:.2f}s")
    return req_id, all_questions, category_times, total_time

