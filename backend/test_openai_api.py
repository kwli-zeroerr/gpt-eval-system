#!/usr/bin/env python3
"""测试 OpenAI API 连接和 token 是否有效"""
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
import httpx

# 加载环境变量
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    load_dotenv(dotenv_path=env_path)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

def test_openai_api():
    """测试 OpenAI API 连接"""
    if not OPENAI_API_KEY:
        print("❌ 错误: OPENAI_API_KEY 未设置")
        return False
    
    # 构建 URL
    base_url = OPENAI_BASE_URL.rstrip("/")
    if base_url.endswith("/v1"):
        url = f"{base_url}/chat/completions"
    elif "/v1" in base_url:
        url = f"{base_url}/chat/completions"
    else:
        url = f"{base_url}/v1/chat/completions"
    
    print(f"🔗 测试 API 连接...")
    print(f"   URL: {url}")
    print(f"   Model: {OPENAI_MODEL}")
    print(f"   API Key: {OPENAI_API_KEY[:10]}...{OPENAI_API_KEY[-4:]}")
    print()
    
    payload = {
        "model": OPENAI_MODEL,
        "messages": [{"role": "user", "content": "请回复'测试成功'"}],
        "max_tokens": 50,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {OPENAI_API_KEY}",
    }
    
    try:
        with httpx.Client(timeout=30.0, verify=False) as client:
            print("📤 发送请求...")
            resp = client.post(url, json=payload, headers=headers)
            
            print(f"📥 响应状态码: {resp.status_code}")
            
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                    print(f"✅ API 连接成功！")
                    print(f"📝 响应内容: {content}")
                    return True
                except Exception as e:
                    print(f"❌ 解析响应失败: {e}")
                    print(f"响应内容: {resp.text[:500]}")
                    return False
            else:
                print(f"❌ API 请求失败")
                print(f"状态码: {resp.status_code}")
                response_text = resp.text
                
                # 尝试解析错误信息
                try:
                    error_data = resp.json()
                    error_msg = error_data.get("error", {}).get("message", response_text)
                    print(f"错误信息: {error_msg}")
                except:
                    # 检查是否是 HTML 响应（可能是 rate limit 或代理错误）
                    if "<html>" in response_text or "<!DOCTYPE" in response_text:
                        response_lower = response_text.lower()
                        if "rate" in response_lower or "limit" in response_lower:
                            print(f"⚠️  检测到 Rate Limit 错误（404/429）")
                        else:
                            print(f"⚠️  收到 HTML 响应（可能是代理/网关错误）")
                        print(f"响应内容（前500字符）: {response_text[:500]}")
                    else:
                        print(f"错误信息: {response_text[:500]}")
                
                return False
                
    except httpx.TimeoutException:
        print(f"❌ 请求超时（30秒）")
        return False
    except Exception as e:
        print(f"❌ 请求失败: {e}")
        return False

if __name__ == "__main__":
    print("=" * 60)
    print("OpenAI API 连接测试")
    print("=" * 60)
    print()
    
    success = test_openai_api()
    
    print()
    print("=" * 60)
    if success:
        print("✅ 测试通过")
    else:
        print("❌ 测试失败")
    print("=" * 60)
    
    sys.exit(0 if success else 1)

