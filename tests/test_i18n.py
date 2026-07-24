"""Phase 0 regression tests - i18n module."""

import pytest
from bridgelib.i18n import T, set_lang, LANG, _STR


class TestTranslate:
    """Basic tests for T()."""

    def test_known_key_zh(self):
        assert T("window_title", "zh") == "Bridge — AI Agent 协作桥接器"

    def test_known_key_en(self):
        assert T("window_title", "en") == "Bridge — AI Agent Collaboration Hub"

    def test_unknown_key_fallback(self):
        """An unknown key returns the key itself."""
        assert T("nonexistent.key.here", "zh") == "nonexistent.key.here"

    def test_nested_key(self):
        """Nested keys are separated by periods."""
        assert T("mode.architect-engineer.name", "zh") == "Architect-Engineer"
        assert T("mode.peer-review.name", "en") == "Peer-Review"

    def test_stage_key_zh(self):
        assert T("stage.discovery", "zh") == "需求澄清"
        assert T("stage.implement", "zh") == "编码实现"

    def test_stage_key_en(self):
        assert T("stage.discovery", "en") == "Discovery"
        assert T("stage.review", "en") == "Review"

    def test_fmt_parameters(self):
        """Interpolate format parameters."""
        result = T("tmpl.agents_project_info", "zh", name="测试", time="2026-01-01", mode="Test")
        assert "测试" in result
        assert "2026-01-01" in result
        assert "Test" in result

    def test_fallback_when_partial_path(self):
        """A partial path match failure returns the key."""
        assert T("stage.nonexistent_stage", "zh") == "stage.nonexistent_stage"


class TestSetLang:
    """set_lang() and global language switching."""

    def test_default_lang_is_zh(self):
        assert LANG == "zh"

    def test_set_lang_en(self):
        set_lang("en")
        # A from-import does not track rebinding, so use the module reference.
        import bridgelib.i18n as i18n
        assert i18n.LANG == "en"
        # Restore the default.
        set_lang("zh")

    def test_T_uses_global_lang(self):
        """Use the global LANG when lang is omitted."""
        set_lang("en")
        assert T("window_title") == "Bridge — AI Agent Collaboration Hub"
        set_lang("zh")
        assert T("window_title") == "Bridge — AI Agent 协作桥接器"


class TestTranslationCoverage:
    """Translation dictionary completeness checks."""

    def test_all_modes_have_name(self):
        """All modes have Chinese and English names."""
        modes = [
            "architect-engineer", "peer-review", "spec-driven",
            "quick-start", "parallel-team", "loop-engineering",
            "parallel-claim", "custom",
        ]
        for m in modes:
            zh = T(f"mode.{m}.name", "zh")
            en = T(f"mode.{m}.name", "en")
            # Returning the key itself indicates a missing translation.
            assert zh != f"mode.{m}.name", f"Missing zh name for {m}"
            assert en != f"mode.{m}.name", f"Missing en name for {m}"

    def test_all_stages_have_translations(self):
        """All known stage IDs have Chinese and English translations."""
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
        """English templates contain no fixed Chinese copy."""
        en_result = T("agents_roles", "en", a_name="GPT", a_role="Architect",
                       a_model="gpt-5", a_duties="Planning",
                       b_name="Reasonix", b_role="Engineer",
                       b_model="rs-v1", b_duties="Coding")
        # English templates should not contain Chinese characters.
        has_chinese = any('\u4e00' <= ch <= '\u9fff' for ch in en_result)
        assert not has_chinese, f"English template contains Chinese: {en_result[:100]}"
