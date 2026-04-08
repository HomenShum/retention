"""
Rate Limit Handler

Provides exponential backoff, retry logic, and token budget tracking
for resilient API calls.

P2: Rate Limit Handling Implementation
"""

import asyncio
import logging
import re
from typing import Optional, Callable, Any, Dict
from dataclasses import dataclass, field
from datetime import datetime, timezone
import time

logger = logging.getLogger(__name__)


@dataclass
class RateLimitState:
    """Tracks rate limit state for exponential backoff."""
    consecutive_failures: int = 0
    last_failure_time: Optional[float] = None
    base_delay_ms: int = 1000  # 1 second
    max_delay_ms: int = 120000  # 2 minutes max
    max_retries: int = 5
    
    def get_backoff_delay_ms(self) -> int:
        """Calculate exponential backoff delay."""
        if self.consecutive_failures == 0:
            return 0
        # Exponential backoff: base * 2^failures, capped at max
        delay = self.base_delay_ms * (2 ** (self.consecutive_failures - 1))
        return min(delay, self.max_delay_ms)
    
    def record_success(self):
        """Reset state after successful call."""
        self.consecutive_failures = 0
        self.last_failure_time = None
    
    def record_failure(self, retry_after_ms: Optional[int] = None) -> int:
        """Record a failure and return wait time in ms."""
        self.consecutive_failures += 1
        self.last_failure_time = time.time()
        
        if retry_after_ms:
            # Use server-specified retry time if available
            return retry_after_ms
        else:
            return self.get_backoff_delay_ms()
    
    def should_retry(self) -> bool:
        """Check if we should retry based on failure count."""
        return self.consecutive_failures < self.max_retries


@dataclass
class TokenBudget:
    """Tracks token usage to prevent hitting rate limits mid-conversation.
    
    OpenAI rate limits example (gpt-4o):
    - TPM (Tokens Per Minute): 30,000 for Tier 1
    - RPM (Requests Per Minute): 500
    
    We track and budget conservatively to avoid mid-conversation failures.
    """
    tpm_limit: int = 30000  # Default Tier 1 limit
    rpm_limit: int = 500
    tokens_used_this_minute: int = 0
    requests_this_minute: int = 0
    minute_start: float = field(default_factory=time.time)
    safety_margin: float = 0.8  # Only use 80% of limit
    
    def _reset_if_new_minute(self):
        """Reset counters if a new minute has started."""
        now = time.time()
        if now - self.minute_start >= 60:
            self.tokens_used_this_minute = 0
            self.requests_this_minute = 0
            self.minute_start = now
    
    def record_usage(self, input_tokens: int, output_tokens: int):
        """Record token usage from a completed request."""
        self._reset_if_new_minute()
        self.tokens_used_this_minute += input_tokens + output_tokens
        self.requests_this_minute += 1
        
        logger.debug(
            f"Token budget: {self.tokens_used_this_minute}/{self.tpm_limit} TPM, "
            f"{self.requests_this_minute}/{self.rpm_limit} RPM"
        )
    
    def estimate_tokens(self, text: str) -> int:
        """Rough estimation of tokens (4 chars per token)."""
        return len(text) // 4
    
    def can_make_request(self, estimated_tokens: int) -> bool:
        """Check if we have budget for a request of estimated size."""
        self._reset_if_new_minute()
        
        available_tokens = int(self.tpm_limit * self.safety_margin) - self.tokens_used_this_minute
        available_requests = int(self.rpm_limit * self.safety_margin) - self.requests_this_minute
        
        if available_requests <= 0:
            logger.warning("Request rate limit budget exhausted")
            return False
        
        if estimated_tokens > available_tokens:
            logger.warning(f"Token budget insufficient: need {estimated_tokens}, have {available_tokens}")
            return False
        
        return True
    
    def get_wait_time_ms(self) -> int:
        """Get wait time until budget resets (in ms)."""
        self._reset_if_new_minute()
        elapsed = time.time() - self.minute_start
        remaining = max(0, 60 - elapsed)
        return int(remaining * 1000)
    
    def get_remaining_budget(self) -> Dict[str, Any]:
        """Get current budget status."""
        self._reset_if_new_minute()
        return {
            "tokens_used": self.tokens_used_this_minute,
            "tokens_limit": self.tpm_limit,
            "tokens_remaining": max(0, self.tpm_limit - self.tokens_used_this_minute),
            "requests_used": self.requests_this_minute,
            "requests_limit": self.rpm_limit,
            "requests_remaining": max(0, self.rpm_limit - self.requests_this_minute),
            "resets_in_ms": self.get_wait_time_ms()
        }


def parse_rate_limit_error(error: Exception) -> Dict[str, Any]:
    """Parse rate limit error to extract retry information.
    
    Returns:
        Dict with keys: is_rate_limit, retry_after_ms, error_message
    """
    error_str = str(error)
    error_lower = error_str.lower()
    
    is_rate_limit = "rate limit" in error_lower or "ratelimit" in error_lower
    
    retry_after_ms = None
    if is_rate_limit:
        # Try to extract retry time from various formats
        # "Please try again in 292ms"
        match = re.search(r'try again in (\d+)ms', error_str)
        if match:
            retry_after_ms = int(match.group(1))
        else:
            # "Please try again in 1.5s"
            match = re.search(r'try again in ([\d.]+)s', error_str)
            if match:
                retry_after_ms = int(float(match.group(1)) * 1000)
            else:
                # Default to 60 seconds
                retry_after_ms = 60000
    
    return {
        "is_rate_limit": is_rate_limit,
        "retry_after_ms": retry_after_ms,
        "error_message": error_str
    }


# Global rate limit state (per-service)
_rate_limit_states: Dict[str, RateLimitState] = {}
_token_budgets: Dict[str, TokenBudget] = {}


def get_rate_limit_state(service_name: str = "default") -> RateLimitState:
    """Get or create rate limit state for a service."""
    if service_name not in _rate_limit_states:
        _rate_limit_states[service_name] = RateLimitState()
    return _rate_limit_states[service_name]


def get_token_budget(service_name: str = "default", tpm_limit: int = 30000) -> TokenBudget:
    """Get or create token budget for a service."""
    if service_name not in _token_budgets:
        _token_budgets[service_name] = TokenBudget(tpm_limit=tpm_limit)
    return _token_budgets[service_name]


__all__ = [
    "RateLimitState",
    "TokenBudget",
    "parse_rate_limit_error",
    "get_rate_limit_state",
    "get_token_budget",
]

