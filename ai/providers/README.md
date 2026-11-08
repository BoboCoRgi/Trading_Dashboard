# ai/providers

Model adapters behind one interface.

| Module | Role |
| --- | --- |
| `base.py` | Provider contract |
| `factory.py` | Chooses mock, OpenAI, or Anthropic from settings |
| `mock.py` | Default. Copies supplied figures and does not invent news |
| `openai.py` | OpenAI adapter. Used only when selected and a key is set |
| `anthropic.py` | Anthropic adapter. Same rule |

Keys are read from the environment. They are not written into the database context and they are not sent to the dashboard.
