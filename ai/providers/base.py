from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal

from pydantic import BaseModel

from ai.schemas import AnalysisResponse


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: Decimal
    cost_note: str


@dataclass
class AIResult:
    provider: str
    model: str
    model_version: str | None
    prompt_version: str
    structured: AnalysisResponse
    raw_text: str
    usage: Usage
    thinking_level: str | None = None


class AIProviderError(RuntimeError):
    pass


class AIProvider(ABC):
    provider_name: str
    model_name: str

    def __init__(self) -> None:
        self._last_usage: Usage | None = None

    def analyse(self, *, system_prompt: str, user_prompt: str, prompt_version: str | None = None) -> AIResult:
        result = self.structured_response(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=AnalysisResponse,
        )
        if prompt_version:
            result.prompt_version = prompt_version
        level = getattr(self, "thinking_level", None)
        if level:
            result.thinking_level = level
        return self._remember(result)

    @abstractmethod
    def structured_response(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: type[BaseModel],
    ) -> AIResult:
        """Return a validated object. Do not log the prompt."""

    def get_usage(self) -> Usage | None:
        return self._last_usage

    def _remember(self, result: AIResult) -> AIResult:
        self._last_usage = result.usage
        return result
