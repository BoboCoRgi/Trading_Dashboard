# ai

Provider-independent analysis. Deterministic code calculates portfolio figures. This package interprets them and records the result.

Claims are classified as fact, inference, opinion, or unknown. A model is not asked to recompute ratios that Python already produced. Broker credentials and market API keys are not placed in a prompt.

| Path | Role |
| --- | --- |
| `context.py` | Builds the evidence packet from stored figures |
| `claims.py`, `schemas.py` | Claim shape and validation |
| `debate.py` | Cross-review structure for two independent analyses |
| `pricing.py` | Token-cost estimate, labeled as an estimate |
| `triggers.py` | Which analysis triggers are enabled |
| `agents/` | Analyst, skeptic, risk officer, daily brief, synthesis |
| `prompts/` | Versioned prompt text |
| `providers/` | Mock, OpenAI, and Anthropic adapters |

`AI_PROVIDER` defaults to `mock`. An external provider runs only when a key is set and the caller allows it.
