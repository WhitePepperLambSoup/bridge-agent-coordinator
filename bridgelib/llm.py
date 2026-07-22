"""Bridge LLM 集成模块。"""
import json
import urllib.request
import urllib.error
from bridgelib.templates import TEMPLATES

# ═══════════════════════════════════════════════════════════════

def call_llm(api_key, api_base, model, system_prompt, user_prompt, timeout=30):
    """调用 OpenAI 兼容 API"""
    from urllib.parse import urlparse

    api_base = api_base.strip()
    parsed = urlparse(api_base)

    # 安全校验：只允许 https 或真正的 localhost/127.0.0.1
    if parsed.scheme == "https":
        pass  # OK
    elif parsed.scheme == "http":
        # 严格检查 hostname 是否为 localhost 或 127.0.0.1（不能用 startswith）
        allowed_hosts = {"localhost", "127.0.0.1", "::1"}
        if parsed.hostname not in allowed_hosts:
            raise ValueError(
                f"明文 HTTP 仅允许 localhost/127.0.0.1/::1，"
                f"不允许 {parsed.hostname}。请使用 https://。"
            )
    else:
        raise ValueError("API Base URL 必须以 https:// 开头（本地地址 http://localhost 例外）。")
    if len(api_key.strip()) < 8:
        raise ValueError("API Key 过短，可能无效。请检查。")

    url = f"{api_base.rstrip('/')}/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.7,
        "max_tokens": 2000
    }).encode("utf-8")

    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code}: {err_body[:500]}")
    except Exception as e:
        raise RuntimeError(str(e))


def llm_enhance_description(api_key, api_base, model, user_input, mode_name):
    """用 LLM 理解用户输入并增强项目描述"""
    system_prompt = f"""你是一个 AI Agent 协作框架的配置助手。用户选择了「{mode_name}」协作模式。
请根据用户的描述，简洁地提取以下信息（JSON 格式）：
{{
  "project_name": "项目名称",
  "agent_a_role": "Agent A 的具体角色描述（20字以内）",
  "agent_b_role": "Agent B 的具体角色描述（20字以内）",
  "tech_stack": "推荐技术栈",
  "key_features": ["核心功能1", "核心功能2"]
}}
只输出 JSON，不要其他内容。"""

    result = call_llm(api_key, api_base, model, system_prompt, user_input)
    # 尝试提取 JSON
    result = result.strip()
    if result.startswith("```"):
        result = result.split("\n", 1)[1]
        if result.endswith("```"):
            result = result[:-3]
    return json.loads(result)


# ═══════════════════════════════════════════════════════════════
# GUI 界面
