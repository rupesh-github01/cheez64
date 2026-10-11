"""
LLM-Powered Natural Language Chess Coach module.
Provides server-authoritative context compilation, progressive hints,
pluggable LLM provider integration, and deterministic offline fallback.
"""

from .provider import (
    LLMProvider,
    DeterministicFallbackProvider,
    OpenAICompatibleProvider,
    get_llm_provider,
    ProviderError,
    ProviderTimeoutError,
    ProviderAPIError,
)
from .context import (
    build_move_context,
    build_player_context,
    build_grounded_context_prompt,
    validate_game_and_ply,
)
from .hints import (
    generate_progressive_hint,
    check_and_sanitize_hint_leakage,
    HINT_TIER_CONCEPTUAL,
    HINT_TIER_SPECIFIC,
    HINT_TIER_REVEAL,
)
from .service import CoachService

__all__ = [
    "LLMProvider",
    "DeterministicFallbackProvider",
    "OpenAICompatibleProvider",
    "get_llm_provider",
    "ProviderError",
    "ProviderTimeoutError",
    "ProviderAPIError",
    "build_move_context",
    "build_player_context",
    "build_grounded_context_prompt",
    "validate_game_and_ply",
    "generate_progressive_hint",
    "check_and_sanitize_hint_leakage",
    "HINT_TIER_CONCEPTUAL",
    "HINT_TIER_SPECIFIC",
    "HINT_TIER_REVEAL",
    "CoachService",
]
