"""
Core Chess Coach service coordinator.

Handles request sanitization, authoritative context retrieval, hint processing,
LLM provider delegation, error handling, and response formatting.
"""

from typing import Dict, Any, Optional, List
from src.library import GameLibrary
from .provider import (
    LLMProvider,
    get_llm_provider,
    ProviderTimeoutError,
    ProviderAPIError,
)
from .context import (
    validate_game_and_ply,
    build_move_context,
    build_player_context,
    build_grounded_context_prompt,
)
from .hints import (
    generate_progressive_hint,
    check_and_sanitize_hint_leakage,
    HINT_TIER_CONCEPTUAL,
    HINT_TIER_SPECIFIC,
    HINT_TIER_REVEAL,
)
from .prompts import CHESS_COACH_SYSTEM_PROMPT

MAX_QUESTION_LENGTH = 1000
MAX_HISTORY_MESSAGES = 10
MAX_MESSAGE_LENGTH = 1000

SUGGESTED_QUESTIONS = [
    "Why was this move a mistake?",
    "Why is the engine's best move better than my move?",
    "Explain this position in simpler terms.",
    "What should I have considered before playing this move?",
    "What recurring mistakes do I make across my games?",
    "What should I work on next?",
    "Give me a hint without immediately revealing the best move.",
]


class CoachService:
    def __init__(self, provider: Optional[LLMProvider] = None):
        self._provider = provider

    def get_provider(self) -> LLMProvider:
        if self._provider is not None:
            return self._provider
        return get_llm_provider()

    async def chat(
        self,
        library: GameLibrary,
        question: str,
        game_id: Optional[str] = None,
        ply: Optional[int] = None,
        hint_level: Optional[int] = None,
        messages: Optional[List[Dict[str, str]]] = None,
        player: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Main conversational entrypoint.
        """
        # 1. Input sanitization & size limits
        if not question or not question.strip():
            raise ValueError("Question cannot be empty")

        clean_question = question.strip()
        if len(clean_question) > MAX_QUESTION_LENGTH:
            raise ValueError(f"Question exceeds maximum length of {MAX_QUESTION_LENGTH} characters")

        sanitized_history = []
        if messages:
            if len(messages) > MAX_HISTORY_MESSAGES:
                # Keep most recent
                messages = messages[-MAX_HISTORY_MESSAGES:]
            for m in messages:
                if isinstance(m, dict) and m.get("role") in ("user", "assistant"):
                    c = str(m.get("content", ""))[:MAX_MESSAGE_LENGTH]
                    sanitized_history.append({"role": m["role"], "content": c})

        # 2. Server-authoritative context validation & extraction
        game, analysis, move = validate_game_and_ply(library, game_id, ply)
        move_ctx = build_move_context(game, analysis, move)

        # Check if player profile should be queried
        q_lower = clean_question.lower()
        needs_player_profile = any(
            w in q_lower for w in ["recurring", "work on", "practice", "recommend", "weakness", "prioritize", "across my games"]
        )
        player_ctx = build_player_context(library, player) if needs_player_profile else None

        # 3. Handle Hint Mode
        if hint_level in (HINT_TIER_CONCEPTUAL, HINT_TIER_SPECIFIC, HINT_TIER_REVEAL):
            if not move_ctx.get("has_move"):
                hint_response = (
                    "**Hint Mode**: Please select a specific move on the board or in the move list "
                    "to request a hint for that position."
                )
            else:
                hint_response = generate_progressive_hint(move_ctx, hint_level)

            provider = self.get_provider()
            return {
                "response": hint_response,
                "provider": provider.provider_name,
                "is_fallback": provider.is_fallback,
                "hint_level": hint_level,
                "move_context": self._format_move_summary(move_ctx),
                "suggested_questions": SUGGESTED_QUESTIONS,
            }

        # 4. Compile prompt
        grounded_context = build_grounded_context_prompt(
            move_ctx, player_ctx, include_player_profile=needs_player_profile
        )
        full_system_prompt = f"{CHESS_COACH_SYSTEM_PROMPT}\n\n{grounded_context}"

        # 5. Build conversation messages for provider
        provider_messages = list(sanitized_history)
        provider_messages.append({"role": "user", "content": clean_question})

        # 6. Call Provider
        provider = self.get_provider()
        response_text = await provider.complete(
            messages=provider_messages,
            system_prompt=full_system_prompt,
        )

        # 7. Check if response is a hint or contains leakage
        if move_ctx.get("best_move"):
            # If user asked for a hint generally without specifying level
            if "hint" in q_lower and "reveal" not in q_lower and "show best move" not in q_lower:
                response_text = check_and_sanitize_hint_leakage(
                    response_text, move_ctx.get("best_move"), tier=1
                )

        return {
            "response": response_text,
            "provider": provider.provider_name,
            "is_fallback": provider.is_fallback,
            "hint_level": hint_level,
            "move_context": self._format_move_summary(move_ctx),
            "suggested_questions": SUGGESTED_QUESTIONS,
        }

    def _format_move_summary(self, move_ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not move_ctx.get("has_move"):
            return None
        return {
            "ply": move_ctx.get("ply"),
            "move_number": move_ctx.get("move_number"),
            "color": move_ctx.get("color"),
            "played_move": move_ctx.get("played_move"),
            "best_move": move_ctx.get("best_move"),
            "classification": move_ctx.get("classification"),
            "centipawn_loss": move_ctx.get("centipawn_loss"),
        }
