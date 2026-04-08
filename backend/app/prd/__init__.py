"""
PRD Ingestion Module.

Provides functionality for:
- Parsing Product Requirement Documents (PRDs)
- Generating test cases from user stories
- Executing generated tests on device fleet
"""

from .parser import PRDParser
from .test_generator import TestCaseGenerator

__all__ = [
    "PRDParser",
    "TestCaseGenerator",
]

