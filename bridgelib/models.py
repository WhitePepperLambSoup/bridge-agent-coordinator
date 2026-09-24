"""Bridge model registry module."""
import json
import urllib.request
import urllib.error

MODEL_REGISTRY = {
    # ═══════════════════════════════════════════════════════════
    # ⚠️  Snapshot date: 2026-07-21
    # 📡 Authoritative source: https://github.com/modelscan/registry (1,285 models, updated daily)
    #    Cross-checked against official vendor documentation
    # 🔧 Update method: prioritize modelscan/registry and supplement it with official documentation
    # ═══════════════════════════════════════════════════════════

    # ── OpenAI (official docs cross-checked with the registry) ──
    "gpt-5.6-sol":      {"vendor": "OpenAI", "tier": "high",  "cost": "$$$", "notes": "旗舰 $5/$30 per MTok | 1M ctx"},
    "gpt-5.6-terra":    {"vendor": "OpenAI", "tier": "high",  "cost": "$$",  "notes": "平衡 $2.50/$15 | 1M ctx"},
    "gpt-5.6-luna":     {"vendor": "OpenAI", "tier": "mid",   "cost": "$",   "notes": "高性价比 $1/$6 | 1M ctx"},
    "gpt-5.6-sol-pro":  {"vendor": "OpenAI", "tier": "high",  "cost": "$$$", "notes": "GPT-5.6 Sol Pro 模式"},
    "gpt-5.6-terra-pro":{"vendor": "OpenAI", "tier": "high",  "cost": "$$",  "notes": "GPT-5.6 Terra Pro 模式"},
    "gpt-5.6-luna-pro": {"vendor": "OpenAI", "tier": "mid",   "cost": "$",   "notes": "GPT-5.6 Luna Pro 模式"},
    "o3":               {"vendor": "OpenAI", "tier": "high",  "cost": "$$$", "notes": "推理模型"},
    "o4-mini":          {"vendor": "OpenAI", "tier": "high",  "cost": "$$",  "notes": "轻量推理"},

    # ── Anthropic (official docs cross-checked with the registry) ──
    "claude-fable-5":   {"vendor": "Anthropic", "tier": "high",  "cost": "$$$", "notes": "最强 $10/$50 | 1M ctx"},
    "claude-opus-4-8":  {"vendor": "Anthropic", "tier": "high",  "cost": "$$",  "notes": "编码+企业 $5/$25 | 1M ctx"},
    "claude-sonnet-5":  {"vendor": "Anthropic", "tier": "high",  "cost": "$$",  "notes": "速度+智能 $3/$15 | 1M ctx"},
    "claude-haiku-4-5": {"vendor": "Anthropic", "tier": "low",   "cost": "$",   "notes": "最快 $1/$5 | 200k ctx"},

    # ── Google (official docs cross-checked with the registry) ──
    "gemini-3.5-flash":       {"vendor": "Google", "tier": "high",  "cost": "$$",  "notes": "最强 agentic/编码"},
    "gemini-3.1-pro":         {"vendor": "Google", "tier": "high",  "cost": "$$",  "notes": "高级推理 (preview)"},
    "gemini-3.1-flash-lite":  {"vendor": "Google", "tier": "low",   "cost": "$",   "notes": "最强性价比"},
    "gemini-2.5-pro":         {"vendor": "Google", "tier": "high",  "cost": "$$$", "notes": "深度推理"},
    "gemini-2.5-flash":       {"vendor": "Google", "tier": "mid",   "cost": "$",   "notes": "价格性能最佳比"},
    "gemini-2.5-flash-lite":  {"vendor": "Google", "tier": "low",   "cost": "$",   "notes": "最快最便宜"},

    # ── DeepSeek (official API docs: api-docs.deepseek.com, 2026-07) ──
    "deepseek-v4-flash": {"vendor": "DeepSeek", "tier": "high",  "cost": "$",   "notes": "V4 Flash ¥1/¥2 per MTok | 1M ctx | 384K output"},
    "deepseek-v4-pro":   {"vendor": "DeepSeek", "tier": "high",  "cost": "$$",  "notes": "V4 Pro ¥3/¥6 per MTok | 1M ctx | 384K output"},
    # ⚠️ deepseek-chat/deepseek-reasoner will be retired on 2026-07-24

    # ── Meta (verified with OpenRouter) ──
    "meta-muse-spark-1.1": {"vendor": "Meta",    "tier": "high",  "cost": "$$",  "notes": "Muse Spark 1.1 (取代 Llama 4) | 1M ctx | agentic"},
    # ── Mistral ──
    "mistral-large":   {"vendor": "Mistral",  "tier": "high",  "cost": "$$",  "notes": "Mistral 旗舰"},
    # ── Chinese models (verified with live OpenRouter data, 2026-07-21) ──
    "kimi-k3":         {"vendor": "Moonshot",  "tier": "high",  "cost": "$$",  "notes": "Kimi K3 2.8T MoE | $3/$15 | 编码 SOTA"},
    "kwaipilot-kat-coder-air":  {"vendor": "Kwaipilot","tier": "high","cost": "$", "notes": "快手 KAT-Coder-Air V2.5 | agentic coding"},
    "kwaipilot-kat-coder-pro":  {"vendor": "Kwaipilot","tier": "high","cost": "$$","notes": "快手 KAT-Coder-Pro V2.5 | $0.74/$2.96"},
    "meituan-longcat-2": {"vendor": "Meituan",  "tier": "high",  "cost": "$",   "notes": "LongCat 2.0 1.6T MoE | 48B active | $0.30/$1.20"},
    "qwen3-max":       {"vendor": "Alibaba",  "tier": "high",  "cost": "$$",  "notes": "通义千问旗舰"},
    "qwen3-plus":      {"vendor": "Alibaba",  "tier": "mid",   "cost": "$",   "notes": "千问中等"},
    "qwen3-turbo":     {"vendor": "Alibaba",  "tier": "low",   "cost": "$",   "notes": "千问快速"},
    "doubao-1.5-pro":  {"vendor": "ByteDance","tier": "high",  "cost": "$",   "notes": "豆包旗舰"},
    "glm-4.5":         {"vendor": "Zhipu",    "tier": "high",  "cost": "$$",  "notes": "智谱旗舰"},

    # ── Local ──
    "local-model":     {"vendor": "Local",    "tier": "varies","cost": "$",   "notes": "本地模型 (Ollama/LM Studio)"},
}

# Authoritative data source URL used to load the latest model list dynamically
MODEL_REGISTRY_SOURCE = "https://raw.githubusercontent.com/modelscan/registry/main/models.json"


def get_models_by_tier(tier=None):
    """Filter models by tier."""
    if tier:
        return {k: v for k, v in MODEL_REGISTRY.items() if v["tier"] == tier}
    return MODEL_REGISTRY

def fetch_latest_models():
    """Fetch the latest model list from modelscan/registry.
    This optional feature is called only when explicitly triggered by the user.
    Returns: (success: bool, data: dict or str)
    """
    try:
        req = urllib.request.Request(MODEL_REGISTRY_SOURCE)
        req.add_header("User-Agent", "Bridge/1.0")
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return True, data
    except Exception as e:
        return False, str(e)

