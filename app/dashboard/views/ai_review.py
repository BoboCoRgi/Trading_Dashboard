from collections import defaultdict
from decimal import Decimal

import streamlit as st

from app.dashboard.charts import ai_cost_chart, count_bar_chart, show_chart
from app.dashboard.components import (
    ai_agent_panel,
    choice_rows,
    evidence_list,
    hero_number,
    metric_strip,
    page_header,
    placeholder_grid,
    section_header,
)
from app.dashboard.present import AGENT_CLASS
from app.dashboard.system_status import database_unavailable_message
from app.dashboard.theme import BLUE, LAVENDER
from core.config import get_settings
from db.session import session_scope
from services.queries import ai_review

_ROLE_TONE = {
    "analyst": "blue",
    "skeptic": "rose",
    "risk": "coral",
    "synthesis": "lavender",
    "brief": "lavender",
}


def render() -> None:
    page_header(
        "AI review",
        "How much the models are used, what that costs, and what is not measured yet.",
    )
    try:
        with session_scope() as session:
            view = ai_review(session)
    except Exception:
        st.error(database_unavailable_message(get_settings().data_mode))
        return

    totals = view["totals"]
    runs = view["runs"]
    models = sorted({row["model"] for row in runs})
    hero_number(f"${Decimal(totals['estimated_cost_usd']):,.4f}", "Estimated cost")
    metric_strip(
        [
            {"value": str(totals["runs"]), "label": "Runs"},
            {"value": str(len(models)), "label": "Models"},
            {
                "value": models[0] if len(models) == 1 else ("—" if not models else "Several"),
                "label": "In use",
            },
        ]
    )
    st.caption("Cost is an estimate from a public rate table, including mock runs. It is not an invoice.")

    by_day: dict[str, float] = defaultdict(float)
    by_provider: dict[str, int] = defaultdict(int)
    by_trigger: dict[str, int] = defaultdict(int)
    for run in runs:
        by_day[run["time"][:10]] += float(Decimal(run["estimated_cost_usd"]))
        by_provider[run["provider"]] += 1
        by_trigger[run["trigger"].replace("_", " ").title()] += 1
    cost_rows = [{"date": day, "cost": amount} for day, amount in sorted(by_day.items())]
    provider_rows = [{"label": name, "count": count} for name, count in sorted(by_provider.items())]
    trigger_rows = [{"label": name, "count": count} for name, count in sorted(by_trigger.items(), key=lambda item: item[1])]

    if cost_rows:
        section_header("What has the estimated cost been?", "Summed by day from stored run estimates.")
        show_chart(ai_cost_chart(cost_rows))
    else:
        section_header("What has the estimated cost been?")
        st.caption("Cost history will appear once a run is stored.")

    left, right = st.columns(2, gap="large")
    with left:
        section_header("Which provider ran?")
        if provider_rows:
            show_chart(count_bar_chart(provider_rows, LAVENDER))
        else:
            st.caption("No runs are stored.")
    with right:
        section_header("What triggered the runs?")
        if trigger_rows:
            show_chart(count_bar_chart(trigger_rows, BLUE))
        else:
            st.caption("No runs are stored.")

    section_header("What value did the models add?")
    placeholder_grid(
        [
            ("Risk alerts later validated", "Not measured yet. Needs a later check against what actually happened."),
            ("Opportunity return, 5D / 20D / 60D", "Not measured yet. Decision markers stay on the decision, not on the model."),
            ("Analyst versus skeptic disagreement", "Not scored yet. The two views are stored, but not reduced to a number."),
            ("Model bias", "Not measured yet."),
        ]
    )

    choice_rows(
        [
            {
                "badge": row["role"].replace("_", " "),
                "tone": _ROLE_TONE.get(AGENT_CLASS.get(row["role"], ""), "grey"),
                "title": row["model"],
                "meta": f"{row['time']} · {row['trigger'].replace('_', ' ').title()} · ${Decimal(row['estimated_cost_usd']):,.4f}",
            }
            for row in runs
        ]
    )
    if not runs:
        return
    labels = [f"{row['role'].replace('_', ' ').title()} · {row['model']} · {row['time']}" for row in runs]
    choice = st.selectbox("Inspect a run", labels)
    run = runs[labels.index(choice)]
    ai_agent_panel(run["role"], run["response"])
    with st.expander("Technical details"):
        st.caption(run["cost_note"])
        st.caption(
            f"{run['provider']} · model version {run['model_version']} · prompt {run['prompt_version']} · "
            f"tokens {run['input_tokens']} in / {run['output_tokens']} out · trigger {run['trigger']}"
        )
        evidence_list(run["claims"])
