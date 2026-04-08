"""
Core abstractions for GPT-5.4 Agentic Vision with Code Execution.

This module provides the building blocks for the GPT-5.4 agentic vision pipeline:
1. LLMProvider: Abstract interface for LLM providers (GPT-5.4 primary)
2. LocalCodeExecutor: Safe sandbox for executing Python code for image manipulation.
3. extract_code_block: Parse Python code blocks from LLM responses.
"""

import abc
import ast
import base64
import contextlib
import io
import logging
from typing import Any, Dict, List, Optional, Union
from pathlib import Path

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------------------
# LLM Provider Abstraction
# ------------------------------------------------------------------------------

class LLMProvider(abc.ABC):
    """Abstract interface for LLM providers."""
    
    @abc.abstractmethod
    async def generate_response(
        self, 
        prompt: str, 
        images: List[Union[bytes, str]] = None,
        system_instruction: str = None
    ) -> str:
        """
        Generate a text response from the LLM.
        
        Args:
            prompt: User prompt
            images: List of image bytes or base64 strings
            system_instruction: Optional system prompt
            
        Returns:
            The text response
        """
        pass

class OpenAIProvider(LLMProvider):
    """OpenAI GPT-4o implementation of LLMProvider."""
    
    def __init__(self, api_key: str, model: str = "gpt-5.4"):
        self.api_key = api_key
        self.model = model
        self.client = None
        
    async def _ensure_client(self):
        if not self.client:
            try:
                from openai import AsyncOpenAI
                self.client = AsyncOpenAI(api_key=self.api_key)
            except ImportError:
                raise ImportError("openai package not installed. Run: pip install openai>=1.0.0")

    async def generate_response(
        self, 
        prompt: str, 
        images: List[Union[bytes, str]] = None,
        system_instruction: str = None
    ) -> str:
        await self._ensure_client()
        
        messages = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
            
        user_content = [{"type": "text", "text": prompt}]
        
        if images:
            for img in images:
                # Convert bytes to base64 if needed
                if isinstance(img, bytes):
                    b64_img = base64.b64encode(img).decode('utf-8')
                else:
                    b64_img = img
                    
                user_content.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{b64_img}"
                    }
                })
        
        messages.append({"role": "user", "content": user_content})
        
        try:
            # Handle o1-like models (like gpt-5.4) which use max_completion_tokens
            kwargs = {
                "model": self.model,
                "messages": messages,
            }
            
            if "gpt-5" in self.model or self.model.startswith("o1-") or "o3-" in self.model:
                kwargs["max_completion_tokens"] = 4096
                # kwargs["temperature"] = 1.0 # Optional: some o1 models only support 1.0
            else:
                kwargs["max_tokens"] = 4096
                kwargs["temperature"] = 0.0
                
            response = await self.client.chat.completions.create(**kwargs)
            return response.choices[0].message.content or ""
        except Exception as e:
            logger.error(f"OpenAI generation failed: {e}")
            raise

# ------------------------------------------------------------------------------
# Local Code Executor
# ------------------------------------------------------------------------------

class LocalCodeExecutor:
    """
    Executes Python code locally in a restricted environment.
    Designed for image manipulation tasks using PIL/Pillow.

    The sandbox exposes a generous set of builtins and pre-imported modules
    so GPT-5.4 can write natural Python for image analysis without fighting
    import restrictions.
    """

    # Modules pre-loaded into every execution context
    _PRELOADED_MODULE_NAMES = frozenset([
        "Image", "ImageDraw", "ImageFont", "ImageFilter", "ImageEnhance",
        "io", "math", "json", "re", "base64", "collections",
    ])

    def __init__(self):
        self.allowed_modules = [
            "PIL", "io", "math", "json", "re", "base64", "collections", "numpy",
        ]

    def execute(self, code: str, globals_dict: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Execute Python code in a sandboxed environment and return results.

        Args:
            code: The Python code to execute
            globals_dict: Optional initial globals (e.g. PIL Images named ``image_0``)

        Returns:
            Dict with keys: success (bool), locals (dict), stdout (str), error (str|None)
        """
        if globals_dict is None:
            globals_dict = {}

        # Track which keys were injected so we can filter them out of results
        injected_keys: set = set()

        # --- Builtins ---
        exec_globals: Dict[str, Any] = {
            "__builtins__": {
                # Core
                "print": print, "range": range, "len": len, "enumerate": enumerate,
                "zip": zip, "map": map, "filter": filter, "sorted": sorted,
                "reversed": reversed, "any": any, "all": all,
                # Types
                "int": int, "float": float, "str": str, "bool": bool, "bytes": bytes,
                "list": list, "dict": dict, "set": set, "tuple": tuple, "type": type,
                # Math
                "abs": abs, "round": round, "min": min, "max": max, "sum": sum,
                "pow": pow, "divmod": divmod,
                # Introspection (safe subset)
                "isinstance": isinstance, "issubclass": issubclass,
                "getattr": getattr, "hasattr": hasattr, "setattr": setattr,
                "callable": callable, "repr": repr, "id": id, "hash": hash,
                "ord": ord, "chr": chr, "hex": hex,
                # Iteration helpers
                "iter": iter, "next": next, "slice": slice,
                # Exceptions (needed for try/except in generated code)
                "Exception": Exception, "ValueError": ValueError,
                "TypeError": TypeError, "KeyError": KeyError,
                "IndexError": IndexError, "AttributeError": AttributeError,
                "RuntimeError": RuntimeError, "StopIteration": StopIteration,
            }
        }
        injected_keys.add("__builtins__")

        # --- Pre-loaded modules ---
        try:
            import PIL.Image, PIL.ImageDraw, PIL.ImageFont
            import PIL.ImageFilter, PIL.ImageEnhance
            for name, mod in [
                ("Image", PIL.Image), ("ImageDraw", PIL.ImageDraw),
                ("ImageFont", PIL.ImageFont), ("ImageFilter", PIL.ImageFilter),
                ("ImageEnhance", PIL.ImageEnhance),
            ]:
                exec_globals[name] = mod
                injected_keys.add(name)
        except ImportError:
            pass

        try:
            import json as _json, re as _re, base64 as _b64
            import collections as _col, math as _math
            for name, mod in [
                ("io", io), ("math", _math), ("json", _json),
                ("re", _re), ("base64", _b64), ("collections", _col),
            ]:
                exec_globals[name] = mod
                injected_keys.add(name)
        except ImportError:
            pass

        # --- User-provided globals (e.g. image_0, original_image, som_elements) ---
        for k, v in globals_dict.items():
            exec_globals[k] = v
            injected_keys.add(k)

        # --- Execute ---
        stdout_capture = io.StringIO()
        try:
            with contextlib.redirect_stdout(stdout_capture):
                exec(code, exec_globals)
        except Exception as e:
            return {
                "success": False,
                "error": f"{type(e).__name__}: {e}",
                "stdout": stdout_capture.getvalue(),
                "locals": {},
            }

        # --- Collect results (only NEW variables created by the code) ---
        result_locals = {
            k: v for k, v in exec_globals.items()
            if k not in injected_keys
        }

        return {
            "success": True,
            "locals": result_locals,
            "stdout": stdout_capture.getvalue(),
            "error": None,
        }

def extract_code_block(text: str) -> Optional[str]:
    """Extract python code block from text."""
    if "```python" in text:
        start = text.find("```python") + 9
        end = text.find("```", start)
        if end != -1:
            return text[start:end].strip()
    elif "```" in text:
        # Fallback for generic blocks
        start = text.find("```") + 3
        end = text.find("```", start)
        if end != -1:
            return text[start:end].strip()
    return None
