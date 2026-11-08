import json

from pydantic import BaseModel

from ai.pricing import estimate_cost_usd
from ai.prompts.v1 import PROMPT_VERSION
from ai.providers.base import AIProvider, AIProviderError, AIResult, Usage
from ai.schemas import AnalysisResponse


class AnthropicProvider(AIProvider):
    provider_name = "anthropic"

    def __init__(self, *, api_key: str, model_name: str) -> None:
        super().__init__()
        if not api_key:
            raise AIProviderError("Anthropic API key is empty")
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
            import anthropic
        except ImportError as exc:
            raise AIProviderError("Install the anthropic package to call Claude") from exc
        client = anthropic.Anthropic(api_key=self._api_key)
        message = client.messages.create(
            model=self.model_name,
            max_tokens=4096,
            system=system_prompt,
            tools=[
                {
                    "name": "submit_analysis",
                    "description": "Submit the structured analysis. This is not an order.",
                    "input_schema": schema.model_json_schema(),
                }
            ],
            tool_choice={"type": "tool", "name": "submit_analysis"},
            messages=[{"role": "user", "content": user_prompt}],
        )
        tool_input = None
        for block in message.content:
            if getattr(block, "type", None) == "tool_use" and block.name == "submit_analysis":
                tool_input = block.input
                break
        if tool_input is None:
            raise AIProviderError("Anthropic response did not include submit_analysis")
        structured = schema.model_validate(tool_input)
        if not isinstance(structured, AnalysisResponse):
            raise AIProviderError("Unexpected schema from Anthropic provider")
        raw = json.dumps(tool_input, default=str)
        usage = message.usage
        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        cost, note = estimate_cost_usd(self.model_name, input_tokens, output_tokens)
        model_version = getattr(message, "model", None)
        result = AIResult(
            provider=self.provider_name,
            model=self.model_name,
            model_version=str(model_version) if model_version else None,
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
