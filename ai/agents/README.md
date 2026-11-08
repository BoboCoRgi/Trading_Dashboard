# ai/agents

The four analysis roles, plus the daily brief.

| Module | Role |
| --- | --- |
| `analyst.py` | Reads the supplied evidence and states a view |
| `skeptic.py` | Looks for unsupported assumptions and missing risks |
| `risk_officer.py` | Reads concentration, drawdown, and scenario impact |
| `daily.py` | Assembles the close review |
| `synthesis.py` | Separates agreement, disagreement, and open questions |

Roles receive the same calculated context. They do not place orders, and they do not invent missing financial figures.
