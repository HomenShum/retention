"""
Progressive Disclosure Pattern Implementation

Based on Anthropic Agent Skills (Oct 2025):
- Load minimal context first (Level 1: metadata only)
- Expand context on-demand (Level 2: full SKILL.md)
- Load additional resources only when needed (Level 3: templates, linked files)

Industry Standard Model Configuration:
- THINKING_MODEL (gpt-5.4): Skill matching, complex routing
- PRIMARY_MODEL (gpt-5-mini): Standard skill execution
- DISTILL_MODEL (gpt-5-nano): ONLY for extracting info from large skill docs
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
import logging
import yaml

logger = logging.getLogger(__name__)

# Model configuration
try:
    from ..model_fallback import THINKING_MODEL, PRIMARY_MODEL, DISTILL_MODEL
except ImportError:
    THINKING_MODEL = "gpt-5.4"
    PRIMARY_MODEL = "gpt-5-mini"
    DISTILL_MODEL = "gpt-5-mini"  # gpt-5-nano is fallback


@dataclass
class SkillMetadata:
    """Level 1: Minimal skill context loaded at startup"""
    name: str
    version: str
    description: str
    triggers: List[str]
    capabilities: List[str]
    dependencies: List[str]
    model_requirements: Dict[str, str]
    files: Dict[str, str]
    path: Path = field(default_factory=Path)
    
    @classmethod
    def from_yaml(cls, yaml_path: Path) -> "SkillMetadata":
        with open(yaml_path) as f:
            data = yaml.safe_load(f)
        return cls(
            name=data.get("name", ""),
            version=data.get("version", "1.0"),
            description=data.get("description", ""),
            triggers=data.get("triggers", []),
            capabilities=data.get("capabilities", []),
            dependencies=data.get("dependencies", []),
            model_requirements=data.get("model_requirements", {}),
            files=data.get("files", {}),
            path=yaml_path.parent,
        )


@dataclass
class SkillContext:
    """Full skill context (Level 2 + Level 3)"""
    metadata: SkillMetadata
    skill_doc: Optional[str] = None  # Level 2: Full SKILL.md
    templates: Dict[str, Any] = field(default_factory=dict)  # Level 3
    linked_docs: Dict[str, str] = field(default_factory=dict)  # Level 3


class ProgressiveDisclosureLoader:
    """
    Loads skill context progressively to minimize context window usage.
    
    Level 1: Load all metadata.yaml files at startup (minimal context)
    Level 2: Load full SKILL.md when task matches skill triggers
    Level 3: Load additional templates/resources only when needed
    """
    
    def __init__(self, skills_dir: Path = None):
        self.skills_dir = skills_dir or Path(__file__).parent / "skills"
        self._metadata_cache: Dict[str, SkillMetadata] = {}
        self._skill_cache: Dict[str, SkillContext] = {}
        self._loaded = False
    
    def load_all_metadata(self) -> Dict[str, SkillMetadata]:
        """Level 1: Load all skill metadata at startup (minimal context)"""
        if self._loaded:
            return self._metadata_cache
        
        if not self.skills_dir.exists():
            logger.warning(f"Skills directory not found: {self.skills_dir}")
            return {}
        
        for skill_dir in self.skills_dir.iterdir():
            if skill_dir.is_dir() and not skill_dir.name.startswith("_"):
                metadata_path = skill_dir / "metadata.yaml"
                if metadata_path.exists():
                    try:
                        metadata = SkillMetadata.from_yaml(metadata_path)
                        self._metadata_cache[metadata.name] = metadata
                        logger.debug(f"Loaded metadata for skill: {metadata.name}")
                    except Exception as e:
                        logger.error(f"Failed to load {metadata_path}: {e}")
        
        self._loaded = True
        logger.info(f"Loaded {len(self._metadata_cache)} skill metadata files")
        return self._metadata_cache
    
    def match_skill(self, query: str) -> Optional[SkillMetadata]:
        """Match a query to the best skill based on triggers"""
        if not self._loaded:
            self.load_all_metadata()
        
        query_lower = query.lower()
        best_match = None
        best_score = 0
        
        for name, metadata in self._metadata_cache.items():
            score = sum(1 for trigger in metadata.triggers if trigger in query_lower)
            if score > best_score:
                best_score = score
                best_match = metadata
        
        if best_match:
            logger.info(f"Matched query to skill: {best_match.name} (score: {best_score})")
        return best_match
    
    def load_skill_doc(self, skill_name: str) -> Optional[str]:
        """Level 2: Load full SKILL.md for a matched skill"""
        if not self._loaded:
            self.load_all_metadata()

        if skill_name in self._skill_cache and self._skill_cache[skill_name].skill_doc:
            return self._skill_cache[skill_name].skill_doc
        
        metadata = self._metadata_cache.get(skill_name)
        if not metadata:
            return None
        
        skill_doc_name = metadata.files.get("skill_doc", "SKILL.md")
        skill_doc_path = metadata.path / skill_doc_name
        
        if skill_doc_path.exists():
            content = skill_doc_path.read_text()
            if skill_name not in self._skill_cache:
                self._skill_cache[skill_name] = SkillContext(metadata=metadata)
            self._skill_cache[skill_name].skill_doc = content
            logger.info(f"Loaded SKILL.md for: {skill_name} ({len(content)} chars)")
            return content
        return None

    # Legacy alias → canonical skill directory name
    _ALIASES: Dict[str, str] = {
        "device_setup": "device_testing",
        "bug_detection": "qa_emulation",
        "bug_reproduction": "qa_emulation",
        "verdict_assembly": "qa_emulation",
        "anomaly_detection": "qa_emulation",
        "test_gen": "test_generation",
        "figma_integration": "figma",
        "figma_flow": "figma",
        "device_test": "device_testing",
    }

    def _resolve_alias(self, name: str) -> str:
        """Resolve a legacy alias to its canonical skill name."""
        return self._ALIASES.get(name, name)

    def get_context_for_skill(self, skill_name: str, level: int = 2) -> Dict[str, Any]:
        """Get context for a specific repo-native skill name."""
        if not self._loaded:
            self.load_all_metadata()

        skill_name = self._resolve_alias(skill_name)
        skill = self._metadata_cache.get(skill_name)
        if not skill:
            return {"matched": False, "level": 0, "skill_name": skill_name}

        context = {
            "matched": True,
            "skill_name": skill.name,
            "level": level,
            "model": skill.model_requirements.get("primary", PRIMARY_MODEL),
            "description": skill.description,
            "capabilities": skill.capabilities,
        }

        if level >= 2:
            skill_doc = self.load_skill_doc(skill.name)
            if skill_doc:
                context["skill_doc"] = skill_doc

        return context
    
    def get_context_for_task(self, task: str, level: int = 2) -> Dict[str, Any]:
        """Get appropriate context for a task based on disclosure level"""
        skill = self.match_skill(task)
        if not skill:
            return {"matched": False, "level": 0}

        return self.get_context_for_skill(skill.name, level=level)

