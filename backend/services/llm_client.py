import os
import logging
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
TIMEOUT_SECONDS = 30


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
        timeout=TIMEOUT_SECONDS, verify=False
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
                error_msg = response_text
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

