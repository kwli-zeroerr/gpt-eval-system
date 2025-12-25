import os
import logging
import time
import threading
from pathlib import Path

import httpx
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# 确保在导入时就加载后端目录下的 .env（避免不同工作目录导致找不到环境变量）
_default_env_path = Path(__file__).resolve().parent.parent / ".env"
if _default_env_path.exists():
    load_dotenv(dotenv_path=_default_env_path)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
# 增加超时时间：连接超时 10 秒，读取超时 120 秒（LLM 响应可能较慢）
TIMEOUT_SECONDS = int(os.getenv("LLM_TIMEOUT", "120"))  # 默认 120 秒
TIMEOUT = httpx.Timeout(connect=10.0, read=TIMEOUT_SECONDS, write=10.0, pool=5.0)

# 请求限流配置
# 最大并发请求数（全局限制，防止触发 rate limit）
MAX_CONCURRENT_REQUESTS = int(os.getenv("LLM_MAX_CONCURRENT", "10"))  # 默认最多 10 个并发请求
# 请求间隔（秒），用于控制请求频率
REQUEST_INTERVAL = float(os.getenv("LLM_REQUEST_INTERVAL", "0.1"))  # 默认 0.1 秒间隔

# 全局请求限流器（使用 Semaphore 控制并发数）
_request_semaphore = threading.Semaphore(MAX_CONCURRENT_REQUESTS)
# 全局请求时间锁（用于控制请求间隔）
_last_request_time = [0.0]  # 使用列表以便在函数中修改
_request_time_lock = threading.Lock()


class LLMConfigError(RuntimeError):
    """Raised when LLM configuration is missing."""


async def call_llm(prompt: str, n: int = 5) -> str:
    """Call the OpenAI-compatible chat completion endpoint.

    This is intentionally minimal; production should add:
    - rate limiting / retries
    - audit logging with request ids
    - streaming if needed
    """
    if not OPENAI_API_KEY:
        raise LLMConfigError("OPENAI_API_KEY not set")

    # 请求限流：控制请求间隔
    import asyncio
    with _request_time_lock:
        current_time = time.time()
        time_since_last = current_time - _last_request_time[0]
        if time_since_last < REQUEST_INTERVAL:
            wait_time = REQUEST_INTERVAL - time_since_last
            await asyncio.sleep(wait_time)
        _last_request_time[0] = time.time()
    
    # 请求限流：控制并发数（使用信号量）
    await asyncio.to_thread(_request_semaphore.acquire)
    try:
        # Build URL - ensure /v1/chat/completions path
        base_url = OPENAI_BASE_URL.rstrip("/")
        if base_url.endswith("/v1"):
            url = f"{base_url}/chat/completions"
        elif "/v1" in base_url:
            # Already has /v1 somewhere, just append /chat/completions
            url = f"{base_url}/chat/completions"
        else:
            # Add /v1 if not present
            url = f"{base_url}/v1/chat/completions"
        
        payload = {
            "model": OPENAI_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 4096,  # Reasonable limit for question generation
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {OPENAI_API_KEY}",
        }
        # Disable SSL verification for development (self-signed certs or expired certs)
        # WARNING: Only use in development, not production
        async with httpx.AsyncClient(
            timeout=TIMEOUT, verify=False
        ) as client:
            resp = await client.post(url, json=payload, headers=headers)
            
            # Log response details only on error
            response_text = resp.text
            
            if resp.status_code != 200:
                # Try to parse error response
                try:
                    error_data = resp.json()
                    error_msg = error_data.get("error", {}).get("message", response_text)
                except:
                    # If response is HTML (like frp 404 page), extract meaningful info
                    if "<html>" in response_text or "<!DOCTYPE" in response_text:
                        # Check if this is a rate limit issue (404 from proxy/gateway due to rate limiting)
                        response_lower = response_text.lower()
                        is_rate_limit = (
                            "rate" in response_lower or 
                            "limit" in response_lower or 
                            "quota" in response_lower or
                            "too many" in response_lower or
                            resp.status_code == 429
                        )
                        if resp.status_code == 404 and is_rate_limit:
                            error_msg = f"Rate limit reached (404 from gateway). URL attempted: {url}"
                        elif resp.status_code == 404:
                            error_msg = f"API endpoint not found (404). Check OPENAI_BASE_URL configuration. URL attempted: {url}"
                        elif resp.status_code == 429:
                            error_msg = f"Rate limit exceeded (429). URL attempted: {url}"
                        else:
                            error_msg = f"API returned {resp.status_code} with HTML response. Check API URL configuration. URL attempted: {url}"
                    else:
                        error_msg = response_text[:500]  # Limit error message length
                # Check for rate limit in error message
                error_lower = error_msg.lower()
                if "rate" in error_lower or "limit" in error_lower or "quota" in error_lower or resp.status_code == 429:
                    raise RuntimeError(f"Rate limit error ({resp.status_code}): {error_msg}")
                raise RuntimeError(f"API returned {resp.status_code}: {error_msg}")
            
            if not response_text.strip():
                raise RuntimeError("Empty response from LLM API")
            
            try:
                data = resp.json()
            except Exception as e:
                logger.error(f"Failed to parse JSON response: {e}")
                logger.error(f"Response text: {response_text}")
                raise RuntimeError(f"Invalid JSON response from LLM API: {response_text[:200]}")
            
            if "choices" not in data or not data["choices"]:
                logger.error(f"Unexpected response structure: {data}")
                raise RuntimeError(f"Unexpected response structure from LLM API: {data}")
            
            return data["choices"][0]["message"]["content"]
    finally:
        # 释放信号量
        _request_semaphore.release()


def call_llm_sync(prompt: str, n: int = 5) -> str:
    """Synchronous version of call_llm for use with ProcessPoolExecutor.
    
    This function is designed to be pickleable for multiprocessing.
    Note: Rate limiting is handled per-process, so each process has its own semaphore.
    """
    if not OPENAI_API_KEY:
        raise LLMConfigError("OPENAI_API_KEY not set")

    # 请求限流：控制请求间隔（同步版本）
    with _request_time_lock:
        current_time = time.time()
        time_since_last = current_time - _last_request_time[0]
        if time_since_last < REQUEST_INTERVAL:
            wait_time = REQUEST_INTERVAL - time_since_last
            time.sleep(wait_time)
        _last_request_time[0] = time.time()
    
    # 请求限流：控制并发数（使用信号量）
    _request_semaphore.acquire()
    try:
        # Build URL - ensure /v1/chat/completions path
        base_url = OPENAI_BASE_URL.rstrip("/")
        if base_url.endswith("/v1"):
            url = f"{base_url}/chat/completions"
        elif "/v1" in base_url:
            # Already has /v1 somewhere, just append /chat/completions
            url = f"{base_url}/chat/completions"
        else:
            # Add /v1 if not present
            url = f"{base_url}/v1/chat/completions"
        
        payload = {
            "model": OPENAI_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 4096,  # Reasonable limit for question generation
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {OPENAI_API_KEY}",
        }
        
        # Use httpx.Client for synchronous requests
        with httpx.Client(timeout=TIMEOUT, verify=False) as client:
            resp = client.post(url, json=payload, headers=headers)
            
            # Log response details only on error
            response_text = resp.text
            
            if resp.status_code != 200:
                # Try to parse error response
                try:
                    error_data = resp.json()
                    error_msg = error_data.get("error", {}).get("message", response_text)
                except:
                    # If response is HTML (like frp 404 page), extract meaningful info
                    if "<html>" in response_text or "<!DOCTYPE" in response_text:
                        # Check if this is a rate limit issue (404 from proxy/gateway due to rate limiting)
                        response_lower = response_text.lower()
                        is_rate_limit = (
                            "rate" in response_lower or 
                            "limit" in response_lower or 
                            "quota" in response_lower or
                            "too many" in response_lower or
                            resp.status_code == 429
                        )
                        if resp.status_code == 404 and is_rate_limit:
                            error_msg = f"Rate limit reached (404 from gateway). URL attempted: {url}"
                        elif resp.status_code == 404:
                            error_msg = f"API endpoint not found (404). Check OPENAI_BASE_URL configuration. URL attempted: {url}"
                        elif resp.status_code == 429:
                            error_msg = f"Rate limit exceeded (429). URL attempted: {url}"
                        else:
                            error_msg = f"API returned {resp.status_code} with HTML response. Check API URL configuration. URL attempted: {url}"
                    else:
                        error_msg = response_text[:500]  # Limit error message length
                # Check for rate limit in error message
                error_lower = error_msg.lower()
                if "rate" in error_lower or "limit" in error_lower or "quota" in error_lower or resp.status_code == 429:
                    raise RuntimeError(f"Rate limit error ({resp.status_code}): {error_msg}")
                raise RuntimeError(f"API returned {resp.status_code}: {error_msg}")
            
            if not response_text.strip():
                raise RuntimeError("Empty response from LLM API")
            
            try:
                data = resp.json()
            except Exception as e:
                logger.error(f"Failed to parse JSON response: {e}")
                logger.error(f"Response text: {response_text}")
                raise RuntimeError(f"Invalid JSON response from LLM API: {response_text[:200]}")
            
            if "choices" not in data or not data["choices"]:
                logger.error(f"Unexpected response structure: {data}")
                raise RuntimeError(f"Unexpected response structure from LLM API: {data}")
            
            return data["choices"][0]["message"]["content"]
    finally:
        # 释放信号量
        _request_semaphore.release()

