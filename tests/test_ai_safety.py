import json
import logging

import pytest

from ai.context import UnsafeAIContext, sanitize_context
from ai.debate import run_debate
from ai.providers.mock import MockProvider
from ai.schemas import AnalysisResponse
from core.enums import AIRole, ClaimType
from core.logging import SecretRedactionFilter, redact_text


def _context() -> dict:
    return {
        "data_origin": "MOCK",
        "label": "MOCK DATA",
        "totals": {
            "snapshot_time": "2026-09-25T21:00:00+00:00",
            "total_gbp": "100000.00",
            "cash_gbp": "12000.00",
            "invested_gbp": "88000.00",
            "daily_pnl_gbp": "-500.00",
            "drawdown_from_ath": "-0.0800",
            "top1_concentration": "0.3200",
            "top3_concentration": "0.6100",
            "top5_concentration": "0.8400",
            "largest_symbol": "RKLB",
        },
        "changes": [{"message": "RKLB weight moved by 0.02."}],
        "scenarios": [
            {
                "name": "RKLB -30%",
                "portfolio_impact": "-0.0960",
                "assumption": "Applies -30% to RKLB only.",
            }
        ],
        "alerts": [{"code": "CONCENTRATION", "message": "RKLB is 0.3200 of the portfolio."}],
        "thesis": {"title": "Launch cadence", "status": "ACTIVE", "updated_at": "2026-02-10"},
        "decision": {"decision_type": "HOLD"},
        "positions": [{"symbol": "RKLB", "account_display_name": "Trading 212 Stocks ISA", "portfolio_weight": "0.3200"}],
    }


def test_context_rejects_account_refs_and_secrets():
    with pytest.raises(UnsafeAIContext):
        sanitize_context({"external_account_ref": "MOCK-T212-ISA-001"})
    with pytest.raises(UnsafeAIContext):
        sanitize_context({"note": "key sk-ant-secretvalue"})


def test_mock_debate_copies_facts_and_does_not_instruct():
    runs = run_debate(MockProvider(), _context())
    assert [item.role for item in runs] == [
        AIRole.ANALYST,
        AIRole.SKEPTIC,
        AIRole.RISK_OFFICER,
        AIRole.SYNTHESIS,
    ]
    for item in runs:
        body = item.result.structured
        assert isinstance(body, AnalysisResponse)
        assert body.is_trading_instruction is False
        assert "not an instruction" in body.disclaimer.lower()
        blob = json.dumps(body.model_dump(mode="json"))
        assert "MOCK-T212-ISA" not in blob
        assert "sk-" not in blob
        assert "-0.0800" in blob
    analyst_facts = runs[0].result.structured.important_facts
    assert any(claim.claim_type is ClaimType.FACT and "100000.00" in claim.text for claim in analyst_facts)
    assert any(claim.claim_type is ClaimType.UNKNOWN for claim in runs[0].result.structured.missing_information)
    assert runs[3].result.structured.areas_of_agreement
    assert runs[3].result.structured.areas_of_disagreement
    usage = runs[0].result.usage
    assert usage.input_tokens > 0
    assert usage.estimated_cost_usd > 0
    assert "MOCK" in usage.cost_note


def test_mock_does_not_invent_a_missing_metric():
    context = _context()
    del context["totals"]["drawdown_from_ath"]
    runs = run_debate(MockProvider(), context)
    texts = " ".join(claim.text for claim in runs[0].result.structured.important_facts)
    assert "Drawdown from the portfolio high was not in the calculated context" in texts


def test_log_filter_redacts_keys():
    assert "sk-ant-" not in redact_text("using sk-ant-abc123XYZ")
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "token=supersecretvalue", (), None)
    assert SecretRedactionFilter().filter(record) is True
    assert "supersecretvalue" not in record.msg
