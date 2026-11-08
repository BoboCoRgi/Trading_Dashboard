from pydantic import BaseModel

from ai.pricing import estimate_cost_usd
from ai.prompts.v1 import PROMPT_VERSION
from ai.providers.base import AIProvider, AIProviderError, AIResult, Usage
from ai.schemas import AnalysisResponse


class OpenAIProvider(AIProvider):
    provider_name = "openai"

    def __init__(self, *, api_key: str, model_name: str) -> None:
        super().__init__()
        if not api_key:
            raise AIProviderError("OpenAI API key is empty")
        self.model_name = model_name
        self._api_key = api_key

    def structured_response(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: type[BaseModel],
    ) -> AIResult:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise AIProviderError("Install the openai package to call OpenAI") from exc
        client = OpenAI(api_key=self._api_key)
        completion = client.chat.completions.create(
            model=self.model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "analysis",
                    "schema": schema.model_json_schema(),
                    "strict": False,
                },
            },
        )
        raw = completion.choices[0].message.content or ""
        structured = schema.model_validate_json(raw)
        if not isinstance(structured, AnalysisResponse):
            raise AIProviderError("Unexpected schema from OpenAI provider")
        usage = completion.usage
        input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        cost, note = estimate_cost_usd(self.model_name, input_tokens, output_tokens)
        result = AIResult(
            provider=self.provider_name,
            model=self.model_name,
            model_version=completion.model,
            prompt_version=PROMPT_VERSION,
            structured=structured,
            raw_text=raw,
            usage=Usage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                estimated_cost_usd=cost,
                cost_note=note,
            ),
        )
        return self._remember(result)
