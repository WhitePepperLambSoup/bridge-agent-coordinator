"""Phase 0 回归测试 — i18n 模块"""

import pytest
from bridgelib.i18n import T, set_lang, LANG, _STR


class TestTranslate:
    """T() 函数基础测试"""

    def test_known_key_zh(self):
        assert T("window_title", "zh") == "Bridge — AI Agent 协作桥接器"

    def test_known_key_en(self):
        assert T("window_title", "en") == "Bridge — AI Agent Collaboration Hub"

    def test_unknown_key_fallback(self):
        """未知 key 返回 key 本身"""
        assert T("nonexistent.key.here", "zh") == "nonexistent.key.here"

    def test_nested_key(self):
        """点号分隔的多级 key"""
        assert T("mode.architect-engineer.name", "zh") == "Architect-Engineer"
        assert T("mode.peer-review.name", "en") == "Peer-Review"

    def test_stage_key_zh(self):
        assert T("stage.discovery", "zh") == "需求澄清"
        assert T("stage.implement", "zh") == "编码实现"

    def test_stage_key_en(self):
        assert T("stage.discovery", "en") == "Discovery"
        assert T("stage.review", "en") == "Review"

    def test_fmt_parameters(self):
        """format 参数插值"""
        result = T("tmpl.agents_project_info", "zh", name="测试", time="2026-01-01", mode="Test")
        assert "测试" in result
        assert "2026-01-01" in result
        assert "Test" in result

    def test_fallback_when_partial_path(self):
        """部分路径匹配失败返回 key"""
        assert T("stage.nonexistent_stage", "zh") == "stage.nonexistent_stage"


class TestSetLang:
    """set_lang() 与全局语言切换"""

    def test_default_lang_is_zh(self):
        assert LANG == "zh"

    def test_set_lang_en(self):
        set_lang("en")
        # from-import 不跟踪 rebinding，使用模块引用
        import bridgelib.i18n as i18n
        assert i18n.LANG == "en"
        # 恢复默认
        set_lang("zh")

    def test_T_uses_global_lang(self):
        """不传 lang 时使用全局 LANG"""
        set_lang("en")
        assert T("window_title") == "Bridge — AI Agent Collaboration Hub"
        set_lang("zh")
        assert T("window_title") == "Bridge — AI Agent 协作桥接器"


class TestTranslationCoverage:
    """翻译字典完整性检查"""

    def test_all_modes_have_name(self):
        """七个模式都有中英文名称"""
        modes = [
            "architect-engineer", "peer-review", "spec-driven",
            "quick-start", "parallel-team", "loop-engineering",
            "parallel-claim", "custom",
        ]
        for m in modes:
            zh = T(f"mode.{m}.name", "zh")
            en = T(f"mode.{m}.name", "en")
            # 不应返回 key 本身（说明翻译缺失）
            assert zh != f"mode.{m}.name", f"Missing zh name for {m}"
            assert en != f"mode.{m}.name", f"Missing en name for {m}"

    def test_all_stages_have_translations(self):
        """所有已知阶段 ID 有中英文翻译"""
        stage_ids = [
            "discovery", "architecture", "task_breakdown", "implement",
            "self_test", "review", "fix", "escalation", "acceptance",
            "plan_together", "claim_tasks", "parallel_work", "merge_review",
            "fix_merge", "final_accept", "plan", "build", "check",
            "impl_review", "integration", "signoff", "spec_init",
            "requirements", "design", "tasks", "parallel_impl",
            "cross_review", "merge_test", "joint_accept", "goal",
        ]
        for sid in stage_ids:
            key = f"stage.{sid}"
            zh = T(key, "zh")
            en = T(key, "en")
            assert zh != key, f"Missing zh for {key}"
            assert en != key, f"Missing en for {key}"

    def test_no_mixed_language_in_en_templates(self):
        """英文模板不混入中文固定文案"""
        en_result = T("agents_roles", "en", a_name="GPT", a_role="Architect",
                       a_model="gpt-5", a_duties="Planning",
                       b_name="Reasonix", b_role="Engineer",
                       b_model="rs-v1", b_duties="Coding")
        # 英文模板不应包含中文字符
        has_chinese = any('\u4e00' <= ch <= '\u9fff' for ch in en_result)
        assert not has_chinese, f"English template contains Chinese: {en_result[:100]}"
