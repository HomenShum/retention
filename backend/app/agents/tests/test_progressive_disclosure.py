"""
Tests for Progressive Disclosure Pattern (Industry Standard - January 2026)

Based on Anthropic Agent Skills (Oct 2025):
- Level 1: Load minimal metadata at startup
- Level 2: Load full SKILL.md when task matches
- Level 3: Load additional resources as needed
"""

import pytest
from app.agents.orchestration.progressive_disclosure import (
    ProgressiveDisclosureLoader,
    SkillMetadata,
)


class TestSkillMetadata:
    """Test skill metadata loading"""

    def test_metadata_loads_from_yaml(self, tmp_path):
        """Metadata should load from YAML file"""
        metadata_file = tmp_path / "metadata.yaml"
        metadata_file.write_text("""
name: test_skill
version: "1.0"
description: A test skill
triggers:
  - test
  - example
capabilities:
  - testing
dependencies:
  - pytest
model_requirements:
  primary: gpt-5-mini
  orchestration: gpt-5.4
files:
  skill_doc: SKILL.md
""")
        metadata = SkillMetadata.from_yaml(metadata_file)
        assert metadata.name == "test_skill"
        assert metadata.version == "1.0"
        assert "test" in metadata.triggers
        assert metadata.model_requirements["primary"] == "gpt-5-mini"
        assert metadata.model_requirements["orchestration"] == "gpt-5.4"


class TestProgressiveDisclosureLoader:
    """Test the progressive disclosure loader"""

    @pytest.fixture
    def loader(self):
        return ProgressiveDisclosureLoader()

    def test_load_all_metadata_returns_dict(self, loader):
        """Should return dict of skill metadata"""
        metadata = loader.load_all_metadata()
        assert isinstance(metadata, dict)

    def test_device_testing_skill_exists(self, loader):
        """device_testing skill should be loaded"""
        metadata = loader.load_all_metadata()
        assert "device_testing" in metadata

    def test_test_generation_skill_exists(self, loader):
        """test_generation skill should be loaded"""
        metadata = loader.load_all_metadata()
        assert "test_generation" in metadata

    def test_qa_emulation_skill_exists(self, loader):
        """qa_emulation skill should be loaded"""
        metadata = loader.load_all_metadata()
        assert "qa_emulation" in metadata


class TestSkillMatching:
    """Test skill matching based on triggers"""

    @pytest.fixture
    def loader(self):
        loader = ProgressiveDisclosureLoader()
        loader.load_all_metadata()
        return loader

    @pytest.mark.parametrize("query,expected_skill", [
        ("Run device testing on Android", "device_testing"),
        ("Launch app on mobile emulator", "device_testing"),
        ("Navigate to settings using adb", "device_testing"),
        ("Generate test cases from PRD", "test_generation"),
        ("Create golden bugs from feature requirements", "test_generation"),
        ("Reproduce bug across regression builds and assemble verdict", "qa_emulation"),
    ])
    def test_skill_matching(self, loader, query, expected_skill):
        """Queries should match appropriate skills"""
        skill = loader.match_skill(query)
        assert skill is not None, f"No skill matched for: {query}"
        assert skill.name == expected_skill

    def test_no_match_returns_none(self, loader):
        """Unknown queries should return None"""
        skill = loader.match_skill("completely unrelated query xyz123")
        assert skill is None


class TestContextLoading:
    """Test progressive context loading levels"""

    @pytest.fixture
    def loader(self):
        loader = ProgressiveDisclosureLoader()
        loader.load_all_metadata()
        return loader

    def test_level_1_context(self, loader):
        """Level 1 should include metadata only"""
        context = loader.get_context_for_task("Run device test", level=1)
        assert context["matched"] is True
        assert context["level"] == 1
        assert "description" in context
        assert "skill_doc" not in context  # Not loaded at Level 1

    def test_level_2_context(self, loader):
        """Level 2 should include full skill doc"""
        context = loader.get_context_for_task("Run device test", level=2)
        assert context["matched"] is True
        assert context["level"] == 2
        assert "skill_doc" in context
        assert len(context["skill_doc"]) > 100  # Full doc loaded

    def test_direct_skill_context_loading(self, loader):
        """Repo-native skills should load by explicit name"""
        context = loader.get_context_for_skill("qa_emulation", level=2)
        assert context["matched"] is True
        assert context["skill_name"] == "qa_emulation"
        assert "skill_doc" in context
        assert "Workflow" in context["skill_doc"]


class TestModelRequirements:
    """Test that skills use correct models"""

    @pytest.fixture
    def loader(self):
        loader = ProgressiveDisclosureLoader()
        loader.load_all_metadata()
        return loader

    def test_device_testing_uses_gpt_5_mini_primary(self, loader):
        """device_testing should use gpt-5-mini as primary"""
        metadata = loader._metadata_cache["device_testing"]
        assert metadata.model_requirements["primary"] == "gpt-5-mini"

    def test_device_testing_uses_gpt_5_4_orchestration(self, loader):
        """device_testing should use gpt-5.4 for orchestration (Mar 2026 flagship)"""
        metadata = loader._metadata_cache["device_testing"]
        assert metadata.model_requirements["orchestration"] == "gpt-5.4"

    def test_test_generation_uses_gpt_5_4_primary(self, loader):
        """test_generation should use gpt-5.4 as primary (complex reasoning)"""
        metadata = loader._metadata_cache["test_generation"]
        assert metadata.model_requirements["primary"] == "gpt-5.4"

    def test_distillation_uses_gpt_5_mini(self, loader):
        """Both skills should use gpt-5-mini for distillation (nano as fallback)"""
        for metadata in loader._metadata_cache.values():
            if "distillation" in metadata.model_requirements:
                # Accept either gpt-5-mini (new) or gpt-5-nano (legacy)
                assert metadata.model_requirements["distillation"] in ["gpt-5-mini", "gpt-5-nano"]

