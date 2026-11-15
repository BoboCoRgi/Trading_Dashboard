"""Displayed numbers use two decimal places, and chart labels have room."""

from app.dashboard.charts import allocation_chart, price_volume_chart, scenario_impact_chart
from app.dashboard.present import format_gbp, tidy_quantity, two_places


def test_displayed_numbers_use_two_decimal_places():
    assert format_gbp(61140) == "£61,140.00"
    assert format_gbp(-10.032) == "-£10.03"
    assert tidy_quantity("1350.69798074") == "1,350.70"
    assert two_places("0.01373398") == "0.01"


def test_chart_labels_are_given_room():
    bars = [
        {"time": "2026-10-01", "close": 61.53, "volume": 1200.5},
        {"time": "2026-10-02", "close": 62.1, "volume": 980.25},
    ]
    fig = price_volume_chart(
        bars,
        style="line",
        trades=[],
        cost_lines=[],
        averages={"MA20": [None, 61.8]},
        show_volume=True,
        zones=[{"kind": "support", "low": 60.0, "high": 61.0, "hover": "Support"}],
    )
    assert fig.layout.showlegend is True
    assert fig.layout.legend.orientation == "h"
    assert fig.layout.legend.y >= 1
    assert fig.layout.margin.t >= 100
    assert fig.layout.margin.l >= 48
    assert fig.layout.margin.b >= 48
    assert fig.layout.yaxis.automargin is True
    assert fig.layout.yaxis.tickformat == ",.2f"
    assert fig.layout.xaxis2.showticklabels is not False
    names = [trace.name for trace in fig.data]
    assert names.count("Support") == 1
    assert "Close" in names
    assert "Volume" in names

    bars_chart = allocation_chart(
        [
            {"label": "Rocket Lab", "weight": 85.04, "value": 61140.2, "kind": "stock"},
            {"label": "SpaceX", "weight": 14.96, "value": 10700.4, "kind": "stock"},
        ]
    )
    assert bars_chart.layout.yaxis.automargin is True
    assert bars_chart.layout.margin.l >= 16
    assert bars_chart.layout.margin.r >= 100
    assert bars_chart.data[0].text[0].endswith("%")
    assert "," in bars_chart.data[0].text[0] or "." in bars_chart.data[0].text[0]

    scenario = scenario_impact_chart(
        [
            {"name": "Nasdaq -10% (beta = 1)", "impact": -10.0, "assumption": ""},
            {"name": "Semiconductor theme -20%", "impact": -21.25, "assumption": ""},
        ]
    )
    labels = [item.text for item in scenario.layout.annotations]
    assert "-10.00%" in labels
    assert "-21.25%" in labels
    assert all(item.x == 0 for item in scenario.layout.annotations)
    assert any("Nasdaq -10%" in str(item.y) for item in scenario.layout.annotations)
    assert scenario.layout.margin.l >= 140
    assert scenario.layout.xaxis.range[1] > 0
