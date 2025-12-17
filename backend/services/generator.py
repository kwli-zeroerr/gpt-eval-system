import uuid
import logging
import time
import asyncio
from typing import Dict, List, Optional, Callable, Awaitable, Tuple
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
from .llm_client import call_llm
from .templates import get_category_dict, make_prompt
from .minio_client import MinIOClient

logger = logging.getLogger(__name__)


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
    
    # Ensure we return at least some questions, even if parsing failed
    if not questions:
        # Last resort: split by sentences or newlines
        parts = re.split(r'[.!?]\s+', raw)
        parts = [p.strip() for p in parts if p.strip() and len(p.strip()) > 10][:count]
        questions = [QuestionItem(category=cat_id, text=p, reference=reference) for p in parts]
    
    return questions[:count]  # Limit to requested count


async def generate_questions(
    categories: List[str],
    per_category: int,
    prompt_overrides: Dict[str, str],
    docs: Optional[List[str]] = None,
    progress_callback: Optional[Callable[[str, int, int, Optional[float]], Awaitable[None]]] = None,
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

    # Generate questions for each category, using different chunks for each question
    all_questions = []
    chunk_index = 0
    current_progress = 0
    
    # Create progress bar
    pbar = tqdm(total=total_questions, desc="生成问题", unit="个")
    
    try:
        for cat_idx, cid in enumerate(categories):
            cat_start_time = time.time()
            hint = prompt_overrides.get(cid) or (cat_dict.get(cid).default_prompt if cid in cat_dict else "")
            pbar.set_description(f"生成 {cid} 问题")
            
            # Generate questions one by one or in small batches to use different chunks
            category_questions = []
            for i in range(per_category):
                # Use different chunks for each question to ensure diversity
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
                    # Use first chunk's reference
                    reference = chunk_refs[0][1] if chunk_refs else ""
                    chunk_index = (chunk_index + 3) % len(all_chunks_with_refs)
                else:
                    # Use single chunk with reference
                    chunk_text, reference = all_chunks_with_refs[chunk_index]
                    context_snippets = [chunk_text]
                    chunk_index = (chunk_index + 1) % len(all_chunks_with_refs)
                
                # Build prompt with specific context
                prompt = make_prompt(
                    category_id=cid,
                    category_hint=hint,
                    count=1,  # Generate one question at a time
                    context_snippets=context_snippets,
                )
                
                # Generate question with reference
                question_results = await _generate_for_category(cid, prompt=prompt, count=1, reference=reference)
                category_questions.extend(question_results)
                
                # Update progress
                current_progress += 1
                pbar.update(1)
                
                # Call progress callback if provided (for WebSocket updates)
                if progress_callback:
                    try:
                        elapsed = time.time() - cat_start_time
                        if asyncio.iscoroutinefunction(progress_callback):
                            await progress_callback(cid, current_progress, total_questions, elapsed)
                        else:
                            progress_callback(cid, current_progress, total_questions, elapsed)
                    except Exception as e:
                        logger.warning(f"Progress callback error: {e}")
            
            cat_elapsed = time.time() - cat_start_time
            category_times[cid] = cat_elapsed
            all_questions.extend(category_questions)
            logger.info(f"Completed {cid}: {len(category_questions)} questions generated in {cat_elapsed:.2f}s")
            
            # Call category complete callback if provided (for real-time display)
            if category_complete_callback:
                try:
                    if asyncio.iscoroutinefunction(category_complete_callback):
                        await category_complete_callback(cid, category_questions, cat_elapsed)
                    else:
                        category_complete_callback(cid, category_questions, cat_elapsed)
                except Exception as e:
                    logger.warning(f"Category complete callback error: {e}")
    finally:
        pbar.close()
    
    total_time = time.time() - start_time
    logger.info(f"Total questions generated: {len(all_questions)} in {total_time:.2f}s")
    return req_id, all_questions, category_times, total_time

