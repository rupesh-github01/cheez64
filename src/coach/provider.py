"""
LLM Provider abstraction and implementations.

Supports:
- OpenAICompatibleProvider: Works with OpenAI, Groq, Ollama, DeepSeek, vLLM via standard HTTP.
- DeterministicFallbackProvider: Completely offline, zero-dependency, rule- and evidence-grounded
  coach fallback used when no API key is configured.
"""

from abc import ABC, abstractmethod
import os
import re
from typing import List, Dict, Any, Optional
import httpx


class ProviderError(Exception):
    """Base exception for LLM provider errors."""
    pass


class ProviderTimeoutError(ProviderError):
    """Raised when an external LLM provider call times out."""
    pass


class ProviderAPIError(ProviderError):
    """Raised when an external LLM provider returns a non-200 error code."""
    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


class LLMProvider(ABC):
    """Abstract interface for chess coach LLM providers."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Name of the provider (e.g. 'openai', 'deterministic_fallback')."""
        pass

    @property
    @abstractmethod
    def is_fallback(self) -> bool:
        """True if running offline deterministic fallback."""
        pass

    @abstractmethod
    async def complete(
        self,
        messages: List[Dict[str, str]],
        system_prompt: str,
        max_tokens: int = 800,
    ) -> str:
        """Generate a response given user messages and system prompt."""
        pass


class OpenAICompatibleProvider(LLMProvider):
    """
    OpenAI-compatible chat completion provider using httpx.
    Compatible with OpenAI (gpt-4o-mini, gpt-4o), Groq, Ollama, etc.
    """

    def __init__(
        self,
        api_key: str,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = 15.0,
    ):
        self.api_key = api_key
        self.model = model or os.environ.get("OPENAI_MODEL") or os.environ.get("CHESS_COACH_LLM_MODEL") or "gpt-4o-mini"
        self.base_url = (base_url or os.environ.get("OPENAI_BASE_URL") or os.environ.get("CHESS_COACH_LLM_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
        self.timeout = float(os.environ.get("CHESS_COACH_LLM_TIMEOUT", timeout))

    @property
    def provider_name(self) -> str:
        return f"openai_compatible:{self.model}"

    @property
    def is_fallback(self) -> bool:
        return False

    async def complete(
        self,
        messages: List[Dict[str, str]],
        system_prompt: str,
        max_tokens: int = 800,
    ) -> str:
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        formatted_messages = [{"role": "system", "content": system_prompt}] + messages

        payload = {
            "model": self.model,
            "messages": formatted_messages,
            "max_tokens": max_tokens,
            "temperature": 0.3,
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(url, headers=headers, json=payload)

            if response.status_code != 200:
                raise ProviderAPIError(
                    f"LLM API returned status {response.status_code}: {response.text[:200]}",
                    status_code=response.status_code,
                )

            data = response.json()
            choices = data.get("choices", [])
            if not choices:
                raise ProviderAPIError("LLM response contained no choices")

            content = choices[0].get("message", {}).get("content", "")
            return content.strip()

        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(
                f"LLM provider request timed out after {self.timeout}s"
            ) from exc
        except httpx.RequestError as exc:
            raise ProviderAPIError(f"Network error contacting LLM provider: {str(exc)}") from exc


class DeterministicFallbackProvider(LLMProvider):
    """
    Offline deterministic coach provider.
    Synthesizes clear, well-structured natural language answers directly from
    authoritative Stockfish analyses, tactical findings, and player recommendations.
    Always available without API keys or network requests.
    """

    def __init__(self):
        pass

    @property
    def provider_name(self) -> str:
        return "deterministic_fallback"

    @property
    def is_fallback(self) -> bool:
        return True

    async def complete(
        self,
        messages: List[Dict[str, str]],
        system_prompt: str,
        max_tokens: int = 800,
    ) -> str:
        """
        Analyze the system prompt (which contains the structured evidence)
        and the user's latest query to return an honest, grounded answer.
        """
        user_query = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                user_query = m.get("content", "").strip()
                break

        query_lower = user_query.lower()

        # Parse context block from system prompt if present
        context = self._extract_context_fields(system_prompt)

        # 1. Player-wide questions: recurring mistakes, recommendations, practice
        if any(w in query_lower for w in ["recurring", "work on", "practice", "recommend", "weakness", "prioritize", "across my games"]):
            return self._answer_player_profile(context, query_lower)

        # 2. Check if move context is missing
        if not context.get("has_move"):
            if not context.get("has_analysis"):
                return (
                    "**Analysis Status**: Analysis is not currently available for this game in the library. "
                    "Run Stockfish analysis on this game first to view move evaluations, tactical findings, and engine recommendations."
                )
            return (
                "**Position Review**: You are currently viewing the starting position or no specific move is selected. "
                "Select a specific move from the move list to explore tactical consequences, candidate moves, or mistakes."
            )

        # 3. Why was this move a mistake?
        if any(w in query_lower for w in ["why was this a mistake", "why is this a mistake", "mistake", "blunder", "inaccuracy", "why bad", "why wrong"]):
            return self._answer_why_mistake(context)

        # 4. Why is engine best move better?
        if any(w in query_lower for w in ["better", "compare", "best move", "alternative", "why engine"]):
            return self._answer_why_best_move_better(context)

        # 5. Explain in simpler terms
        if any(w in query_lower for w in ["simple", "simpler", "plain english", "beginner", "easy terms"]):
            return self._answer_simpler_terms(context)

        # 6. What should I have considered?
        if any(w in query_lower for w in ["consider", "checklist", "what did i miss", "threat"]):
            return self._answer_what_to_consider(context)

        # 7. Default comprehensive grounded response for current position
        return self._answer_general_position(context, user_query)

    def _extract_context_fields(self, prompt: str) -> Dict[str, Any]:
        """Helper to parse key structured fields embedded in the system prompt."""
        data: Dict[str, Any] = {
            "has_move": "NO SPECIFIC MOVE SELECTED" not in prompt,
            "has_analysis": "NO ANALYSIS AVAILABLE" not in prompt,
            "raw_prompt": prompt,
        }

        # Extract basic move attributes
        m = re.search(r"Played Move:\s*([^\n]+)", prompt)
        if m: data["played_move"] = m.group(1).strip()
        m = re.search(r"Best Move:\s*([^\n]+)", prompt)
        if m: data["best_move"] = m.group(1).strip()
        m = re.search(r"Classification:\s*([^\n]+)", prompt)
        if m: data["classification"] = m.group(1).strip()
        m = re.search(r"Centipawn Loss:\s*([^\n]+)", prompt)
        if m: data["cpl"] = m.group(1).strip()
        m = re.search(r"Evaluation Before:\s*([^\n]+)", prompt)
        if m: data["eval_before"] = m.group(1).strip()
        m = re.search(r"Evaluation After:\s*([^\n]+)", prompt)
        if m: data["eval_after"] = m.group(1).strip()
        m = re.search(r"Side to Move:\s*([^\n]+)", prompt)
        if m: data["side"] = m.group(1).strip()
        m = re.search(r"Move Number:\s*([^\n]+)", prompt)
        if m: data["move_number"] = m.group(1).strip()

        # Extract deterministic explanation parts
        m = re.search(r"Explanation Summary:\s*([^\n]+)", prompt)
        if m: data["summary"] = m.group(1).strip()
        m = re.search(r"What Happened:\s*([^\n]+)", prompt)
        if m: data["what_happened"] = m.group(1).strip()
        m = re.search(r"Why It Matters:\s*([^\n]+)", prompt)
        if m: data["why_it_matters"] = m.group(1).strip()
        m = re.search(r"Tactical Motif:\s*([^\n]+)", prompt)
        if m: data["motif"] = m.group(1).strip()
        m = re.search(r"Explanation Variation:\s*([^\n]+)", prompt)
        if m: data["variation"] = m.group(1).strip()
        m = re.search(r"Explanation Limitations:\s*([^\n]+)", prompt)
        if m: data["limitations"] = m.group(1).strip()

        # Tactical finding
        m = re.search(r"Tactical Category:\s*([^\n]+)", prompt)
        if m: data["tactical_category"] = m.group(1).strip()

        # PV after played move (punishment line)
        m = re.search(r"Refutation Line \(pv_after\):\s*([^\n]+)", prompt)
        if m: data["pv_after"] = m.group(1).strip()

        # Engine PV
        m = re.search(r"Engine Recommended Line:\s*([^\n]+)", prompt)
        if m: data["engine_pv"] = m.group(1).strip()

        return data

    def _answer_why_mistake(self, ctx: Dict[str, Any]) -> str:
        played = ctx.get("played_move", "this move")
        cls = ctx.get("classification", "inaccuracy").capitalize()
        raw_cpl = ctx.get("cpl", "N/A")
        cpl = raw_cpl.replace("cp", "").strip()
        what = ctx.get("what_happened")
        why = ctx.get("why_it_matters")
        refutation = ctx.get("pv_after")
        best = ctx.get("best_move")
        cat = ctx.get("tactical_category")
        motif = ctx.get("motif")

        lines = [f"### Why `{played}` is a {cls} (Loss: {cpl} cp)"]

        if what:
            lines.append(f"**What happened**: {what}")
        if why:
            lines.append(f"**Consequence**: {why}")

        if cat and cat.lower() != "none":
            lines.append(f"**Tactical Finding**: Category `{cat}`.")
        elif motif and motif.lower() != "none":
            lines.append(f"**Tactical Motif**: {motif}.")

        if refutation:
            lines.append(f"**Engine Refutation Line**: After `{played}`, the opponent can exploit the position with: `{refutation}`.")
        elif best and best != played:
            lines.append(f"The engine preferred `{best}`, which maintains a stronger position without conceding this advantage.")

        if ctx.get("limitations"):
            lines.append(f"\n> **Note**: {ctx['limitations']}")

        return "\n\n".join(lines)

    def _answer_why_best_move_better(self, ctx: Dict[str, Any]) -> str:
        played = ctx.get("played_move", "your move")
        best = ctx.get("best_move", "the engine move")
        eval_b = ctx.get("eval_before", "0")
        eval_a = ctx.get("eval_after", "0")
        engine_pv = ctx.get("engine_pv", "")
        summary = ctx.get("summary", "")

        lines = [f"### Comparing `{played}` vs. Engine's `{best}`"]

        if summary:
            lines.append(f"**Core difference**: {summary}")

        lines.append(
            f"- **Your Move (`{played}`)**: Evaluation went from {eval_b} cp to {eval_a} cp."
        )
        lines.append(
            f"- **Engine Move (`{best}`)**: Preserves your evaluation around {eval_b} cp."
        )

        if engine_pv:
            lines.append(f"**Key continuation for `{best}`**: `{engine_pv}`")

        what = ctx.get("what_happened")
        if what:
            lines.append(f"While `{played}` allowed tactical/positional issues ({what}), `{best}` avoids them completely.")

        return "\n\n".join(lines)

    def _answer_simpler_terms(self, ctx: Dict[str, Any]) -> str:
        played = ctx.get("played_move", "this move")
        best = ctx.get("best_move", "another move")
        motif = ctx.get("motif", "")
        what = ctx.get("what_happened", "")

        lines = [f"### In Plain English"]
        if what:
            lines.append(f"When you played **{played}**, here is what happened in simple terms: {what}")
        else:
            lines.append(f"Playing **{played}** gave your opponent an opportunity that you didn't need to give them.")

        if motif and motif.lower() != "none":
            lines.append(f"The main theme here is **{motif}**.")

        if best and best != played:
            lines.append(f"Instead of `{played}`, playing **`{best}`** keeps your pieces protected and active.")

        return "\n\n".join(lines)

    def _answer_what_to_consider(self, ctx: Dict[str, Any]) -> str:
        played = ctx.get("played_move", "your move")
        cat = ctx.get("tactical_category", "")
        motif = ctx.get("motif", "")
        what = ctx.get("what_happened", "")

        lines = [f"### What to Consider Before Playing `{played}`"]
        checklist = [
            "1. **Checks, Captures, and Threats**: Always look at what forcing replies your opponent has.",
            "2. **Loose Pieces**: Notice if any piece is left undefended or underdefended.",
        ]

        if "hanging" in cat.lower() or "material" in cat.lower():
            checklist.append("3. **Material Safety**: Before finalizing your move, trace whether the target square leaves an unprotected piece behind.")
        elif "forcing" in cat.lower() or "capture" in cat.lower():
            checklist.append("3. **Missed Opportunities**: Check if you have an active forcing check or capture before making a passive move.")
        else:
            checklist.append("3. **King & Piece Coordination**: Ensure your pieces work together rather than leaving gaps.")

        lines.extend(checklist)

        if what:
            lines.append(f"In this specific position: {what}")

        return "\n\n".join(lines)

    def _answer_player_profile(self, ctx: Dict[str, Any], query: str) -> str:
        prompt = ctx.get("raw_prompt", "")
        # Extract recommendations from prompt if present
        rec_match = re.search(r"=== PLAYER COACHING RECOMMENDATIONS ===\n(.*?)(?:\n===|\Z)", prompt, re.DOTALL)
        if rec_match:
            rec_text = rec_match.group(1).strip()
            return f"### Personalized Coaching Priorities\n\nBased on your analyzed games in the library:\n\n{rec_text}"

        return (
            "### Player Coaching Profile\n\n"
            "To view personalized recommendations, ensure your games are analyzed in the library. "
            "You can also check the **Player Dashboard** for full statistical trends, opening repertoires, and prioritized training areas."
        )

    def _answer_general_position(self, ctx: Dict[str, Any], query: str) -> str:
        played = ctx.get("played_move")
        best = ctx.get("best_move")
        cls = ctx.get("classification")
        cpl = ctx.get("cpl")
        summary = ctx.get("summary")

        lines = [f"### Position Overview"]
        if played and cls:
            lines.append(f"Move played: **{played}** ({cls}, loss: {cpl} cp).")
        if best and best != played:
            lines.append(f"Engine recommendation: **{best}**.")
        if summary:
            lines.append(f"**Analysis**: {summary}")

        lines.append(
            "Feel free to ask:\n"
            "- *Why was this move a mistake?*\n"
            "- *Why is the engine move better?*\n"
            "- *Give me a hint without revealing the best move.*"
        )
        return "\n\n".join(lines)


def get_llm_provider(force_provider: Optional[str] = None) -> LLMProvider:
    """
    Factory function returning the active LLMProvider.
    Respects environment variables:
    - CHESS_COACH_LLM_PROVIDER: 'deterministic' | 'mock' | 'openai'
    - OPENAI_API_KEY / CHESS_COACH_LLM_API_KEY
    """
    provider_name = (
        force_provider
        or os.environ.get("CHESS_COACH_LLM_PROVIDER", "").lower()
    )

    if provider_name in ("deterministic", "mock", "offline"):
        return DeterministicFallbackProvider()

    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("CHESS_COACH_LLM_API_KEY")

    if api_key and provider_name in ("openai", ""):
        return OpenAICompatibleProvider(api_key=api_key)

    # Default fallback when no credentials are configured
    return DeterministicFallbackProvider()
