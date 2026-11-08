"""Reusable presentation. Pages pass already-loaded view dictionaries."""

import html

import streamlit as st

from app.dashboard.present import (
    AGENT_CLASS,
    AGENT_LABEL,
    DECISION_TONE,
    STATUS_TONE,
    attention_items,
    bar_width,
    change_lines,
    confidence_label,
    format_gbp,
    split_pnl,
    summary_map,
    tidy_quantity,
    tone_for_signed,
)
from app.dashboard.theme import INK, TONES, stylesheet

DISCLAIMER = "Analysis is not an instruction. You make the decision. Brokers are read-only."

_CLAIM_SECTIONS = (
    ("Agreement", "areas_of_agreement", False),
    ("Disagreement", "areas_of_disagreement", False),
    ("Counter-evidence", "counter_evidence", True),
    ("Missing", "missing_information", True),
    ("What would change the view", "what_would_change_the_view", False),
    ("Facts", "important_facts", True),
    ("Portfolio implications", "portfolio_risks", True),
    ("Assumptions", "hidden_assumptions", True),
    ("Bull case", "bull_case", True),
    ("Bear case", "bear_case", True),
    ("Opportunities", "opportunities", True),
    ("What changed", "what_changed", False),
)


def inject_css() -> None:
    st.markdown(stylesheet(), unsafe_allow_html=True)


def _esc(value: object) -> str:
    return html.escape("" if value is None else str(value))


def _rich(value: object) -> str:
    text = "" if value is None else str(value)
    if not text.strip():
        return '<span class="meta">—</span>'
    return "<br>".join(_esc(line) for line in text.splitlines())


def show(markup: str) -> None:
    st.html(markup)


def source_banner(source: dict | None) -> str:
    if not source:
        return ""
    mode = source.get("mode")
    stale = bool(source.get("stale"))
    if mode == "live" and stale:
        tone = "stale"
    elif mode == "live":
        tone = "live"
    else:
        tone = "mock"
    badge = status_badge("Stale", "apricot") if stale else ""
    parts = [f'<p class="source-line {tone}">{_esc(source.get("label") or "")}{" " + badge if badge else ""}</p>']
    if source.get("snapshot_clock"):
        parts.append(f'<p class="source-meta">Last snapshot: {_esc(source["snapshot_clock"])}</p>')
    if source.get("age_label"):
        parts.append(f'<p class="source-meta">{_esc(source["age_label"])}</p>')
    if mode == "mock" and source.get("ready"):
        parts.append(
            '<p class="source-meta">Brokers are not connected. Nothing here is an instruction to trade.</p>'
        )
    return f'<div class="source-banner">{"".join(parts)}</div>'


def portfolio_unavailable(source: dict | None) -> None:
    message = (source or {}).get("message") or "No snapshot is stored yet."
    st.warning(message)
    command = (source or {}).get("command")
    if command:
        st.caption("Create a live snapshot, then refresh this page.")
        st.code(command, language="text")


def page_header(title: str, purpose: str, meta: str = "", accent: str = "", source: dict | None = None) -> None:
    inject_css()
    meta_html = f'<div class="meta">{_esc(meta)}</div>' if meta else ""
    rule = (
        f'<div style="width:64px;height:3px;background:{accent};margin:0 0 12px;border-radius:99px"></div>'
        if accent
        else ""
    )
    if source is None:
        banner = '<p class="mock-line">Mock data. Brokers are not connected. Nothing here is an instruction to trade.</p>'
    else:
        banner = source_banner(source)
    show(
        f'{rule}<div class="page-head"><div><h1 class="page-title">{_esc(title)}</h1>'
        f'<p class="page-purpose">{_esc(purpose)}</p></div>{meta_html}</div>'
        f"{banner}"
    )


def section_header(title: str, hint: str = "") -> None:
    hint_html = f'<p class="section-hint">{_esc(hint)}</p>' if hint else ""
    show(f'<h2 class="section-title">{_esc(title)}</h2>{hint_html}')


def empty_state(message: str) -> None:
    show(f'<p class="empty">{_esc(message)}</p>')


def status_badge(label: str, tone: str | None = None) -> str:
    name = tone if tone in TONES else "grey"
    return f'<span class="badge {name}">{_esc(label)}</span>'


def badge_row(pairs: list[tuple[str, str]]) -> None:
    show("".join(status_badge(label, tone) for label, tone in pairs))


def hero_number(value: str, label: str) -> None:
    show(f'<div class="hero-value">{_esc(value)}</div><div class="hero-label">{_esc(label)}</div>')


def metric_strip(items: list[dict]) -> None:
    cells = []
    for item in items:
        tone = item.get("tone") or ""
        tone_class = f" {tone}" if tone in {"pos", "neg"} else ""
        hint = f'<div class="metric-hint">{_esc(item["hint"])}</div>' if item.get("hint") else ""
        cells.append(
            f'<div class="metric"><div class="metric-value{tone_class}">{_esc(item["value"])}</div>'
            f'<div class="metric-label">{_esc(item["label"])}</div>{hint}</div>'
        )
    columns = " cols-4" if len(items) >= 4 else ""
    show(f'<div class="metric-strip{columns}">{"".join(cells)}</div>')


def risk_callout(level: str, title: str, detail: str, tone: str) -> None:
    show(
        f'<div class="callout { _esc(tone) }"><div class="sev">{_esc(level)}</div>'
        f"<h3>{_esc(title)}</h3><p>{_esc(detail)}</p></div>"
    )


def timeline_block(items: list[dict]) -> None:
    if not items:
        empty_state("No stored timestamp for this event.")
        return
    rows = "".join(
        f'<div class="stack-item"><p class="meta">{_esc(item.get("when"))}</p>'
        f'<p class="prose">{_esc(item.get("text"))}</p></div>'
        for item in items
    )
    show(rows)


def market_event_card(event: dict) -> None:
    review = "Thesis review required" if event.get("thesis_review") else "Thesis not auto-changed"
    peer = (
        f"<p class='prose'>Held peers in {_esc(event.get('theme'))} · {_esc(event.get('peer'))}</p>"
        if event.get("peer")
        else "<p class='meta'>No market benchmark is stored.</p>"
    )
    sigma = f"<p class='prose'>Abnormality {_esc(event.get('abnormality'))}</p>" if event.get("abnormality") else ""
    tone = "high" if event.get("priority") in {"HIGH", "CRITICAL"} else "watch" if event.get("priority") == "WATCH" else "info"
    show(
        f'<div class="callout {tone}"><div class="sev">{_esc(event.get("priority"))}</div>'
        f'<h3>{_esc(event.get("symbol"))} unusual move</h3>'
        f'<p class="prose">Price {_esc(event.get("price_change"))}</p>'
        f'<p class="prose">Portfolio impact {_esc(event.get("impact_gbp"))} · {_esc(event.get("impact_pct"))}</p>'
        f"{peer}{sigma}"
        f'<p class="meta">Classification {_esc(event.get("classification"))} · {review}</p>'
        f'<p class="meta">{_esc(event.get("note"))}</p></div>'
    )


def opportunity_card(row: dict) -> None:
    relative = f" · vs held peers {_esc(row.get('relative'))}" if row.get("relative") else ""
    sigma = f" · {_esc(row.get('abnormality'))}" if row.get("abnormality") else ""
    show(
        '<div class="panel" style="border-top:3px solid #ACA0C4">'
        f'<div class="ticker">{_esc(row.get("symbol"))}</div>'
        f'<p class="prose">{_esc(row.get("type"))}</p>'
        f'<p class="prose">Today {_esc(row.get("price_change"))}{relative}{sigma}</p>'
        f'<p class="mini-head">Why it was flagged</p><p class="prose">{_esc(row.get("why"))}</p>'
        f'<p class="mini-head">Why the market may be right</p><p class="prose">{_esc(row.get("why_market"))}</p>'
        f'<p class="prose"><strong>Portfolio fit { _esc(row.get("fit")) }</strong></p>'
        f'<p class="meta">{_esc(row.get("fit_reason"))}</p>'
        f'<p class="meta">Research priority {_esc(row.get("priority"))} · Confidence {_esc(row.get("confidence"))} · {_esc(row.get("status"))}</p>'
        "</div>"
    )


def attention_block(view: dict) -> None:
    section_header("Attention")
    for item in attention_items(view):
        risk_callout(item["level"], item["title"], item["detail"], item["tone"])


def change_block(changes: list[dict]) -> None:
    section_header("What changed")
    lines = change_lines(changes)
    if not lines:
        empty_state("No material change versus the previous snapshot.")
        return
    rows = "".join(
        f'<div class="stack-item"><strong>{_esc(line["title"])}</strong>'
        f'<div class="prose">{_esc(line["message"])}</div></div>'
        for line in lines
    )
    show(rows)


def _bar(weight_text: str) -> str:
    width = bar_width(weight_text)
    return f'<div class="bar" aria-hidden="true"><span style="width:{width}%"></span></div>'


def _daily_cell(amount: float | None) -> str:
    if amount is None:
        return '<span class="meta">—</span>'
    tone = tone_for_signed(format_gbp(amount), gains=True)
    css = f' class="{tone}"' if tone else ""
    return f"<span{css}>{_esc(format_gbp(amount))}</span>"


def positions_table(rows: list[dict]) -> None:
    section_header(
        "Positions",
        "Each row is one account. Today's market P&L is in the chart above when current prices and previous closes exist.",
    )
    if not rows:
        empty_state("No open positions in this snapshot.")
        return
    body = []
    for row in rows:
        pnl = row.get("unrealised") or "—"
        pnl_tone = tone_for_signed(str(pnl), gains=True)
        change = row.get("unrealised_pct") or ""
        body.append(
            "<tr>"
            f'<td><div class="ticker">{_esc(row.get("symbol"))}</div>'
            f'<div class="meta">{_esc(tidy_quantity(str(row.get("quantity") or "")))} · {_esc(row.get("account"))}</div></td>'
            f'<td>{_esc(row.get("value"))}</td>'
            f'<td>{_esc(row.get("weight"))}{_bar(str(row.get("weight") or ""))}</td>'
            f'<td>{_daily_cell(row.get("daily"))}</td>'
            f'<td class="{pnl_tone}">{_esc(pnl)}<div class="meta">{_esc(change)}</div></td>'
            "</tr>"
        )
    show(
        '<div style="overflow-x:auto"><table class="mc-table"><thead><tr>'
        "<th>Ticker</th><th>Value</th><th>Weight</th><th>Today</th><th>Total P&amp;L</th>"
        f'</tr></thead><tbody>{"".join(body)}</tbody></table></div>'
    )


def activity_list(trades: list[dict]) -> None:
    section_header("Latest activity", "Fills in the five days before this snapshot.")
    if not trades:
        empty_state("No fills in that window.")
        return
    rows = []
    for trade in trades:
        tone = DECISION_TONE.get(trade.get("action") or "", "grey")
        rows.append(
            '<div class="stack-item">'
            f'{status_badge(str(trade.get("action") or ""), tone)}'
            f'<strong class="ticker">{_esc(trade.get("symbol"))}</strong> '
            f'<span class="meta">{_esc(tidy_quantity(str(trade.get("quantity") or "")))} @ {_esc(trade.get("price"))}'
            f' · {_esc(trade.get("account"))} · {_esc(trade.get("time"))}</span>'
            "</div>"
        )
    show("".join(rows))


def exposure_rows(title: str, rows: list[dict], name_key: str) -> None:
    section_header(title)
    if not rows:
        empty_state("Nothing to show.")
        return
    body = []
    for row in rows:
        weight = str(row.get("weight") or "")
        body.append(
            "<tr>"
            f'<td><div class="ticker">{_esc(row.get(name_key))}</div></td>'
            f"<td>{_esc(weight)}{_bar(weight)}</td>"
            f'<td class="meta">{_esc(row.get("value"))}</td>'
            "</tr>"
        )
    show(
        '<table class="mc-table"><thead><tr><th></th><th>Weight</th><th>Value</th></tr></thead>'
        f"<tbody>{''.join(body)}</tbody></table>"
    )


def scenario_cards(scenarios: list[dict]) -> None:
    section_header("If this happened", "Cash is left unchanged. Each card states its assumption.")
    cards = []
    for scenario in scenarios:
        impact = str(scenario.get("impact") or "—")
        tone = tone_for_signed(impact)
        cards.append(
            '<div class="panel">'
            f'<div class="scenario-impact {tone}">{_esc(impact)}</div>'
            '<div class="scenario-kicker">Estimated portfolio impact</div>'
            f'<div class="scenario-name">{_esc(scenario.get("name"))}</div>'
            f'<p class="meta">{_esc(scenario.get("assumption"))}</p>'
            "</div>"
        )
    show(f'<div class="card-grid">{"".join(cards)}</div>')


def styled_table(headers: list[str], rows: list[list[str]]) -> None:
    if not rows:
        empty_state("Nothing to compare.")
        return
    head = "".join(f"<th>{_esc(header)}</th>" for header in headers)
    body = []
    for row in rows:
        cells = []
        for index, cell in enumerate(row):
            if index == 0:
                cells.append(f'<td class="ticker">{_esc(cell)}</td>')
                continue
            tone = tone_for_signed(cell)
            css = f' class="{tone}"' if tone else ""
            cells.append(f"<td{css}>{_esc(cell)}</td>")
        body.append(f'<tr>{"".join(cells)}</tr>')
    show(
        f'<div style="overflow-x:auto"><table class="mc-table"><thead><tr>{head}</tr></thead>'
        f'<tbody>{"".join(body)}</tbody></table></div>'
    )


def choice_rows(entries: list[dict]) -> None:
    if not entries:
        empty_state("Nothing here yet.")
        return
    blocks = []
    for entry in entries:
        badge = status_badge(str(entry["badge"]), entry.get("tone")) if entry.get("badge") else ""
        meta = f'<div class="meta">{_esc(entry.get("meta") or "")}</div>' if entry.get("meta") else ""
        detail = f'<div class="prose">{_rich(entry.get("detail"))}</div>' if entry.get("detail") else ""
        blocks.append(
            f'<div class="stack-item">{badge}<strong class="ticker">{_esc(entry.get("title"))}</strong>'
            f"{meta}{detail}</div>"
        )
    show("".join(blocks))


def evidence_list(claims: list[dict]) -> None:
    if not claims:
        empty_state("No stored claims on this run.")
        return
    rows = []
    for claim in claims:
        rows.append(
            {
                "claim_type": claim.get("type") or "UNKNOWN",
                "text": claim.get("text") or "",
            }
        )
    show(_claim_bits(rows, True))


def snapshot_panel(title: str, snapshot: dict | None, empty: str) -> None:
    if not snapshot:
        show(
            f'<div class="panel"><p class="kicker" style="color:{INK}">{_esc(title)}</p>'
            f'<p class="empty">{_esc(empty)}</p></div>'
        )
        return
    rows = [
        ("As of", snapshot.get("as_of")),
        ("Portfolio", snapshot.get("value")),
        ("Cash", snapshot.get("cash")),
        ("Largest weight", snapshot.get("top1")),
        ("Drawdown", snapshot.get("drawdown")),
    ]
    body = "".join(
        f'<div class="stack-item"><span class="meta">{_esc(label)}</span>'
        f'<div class="metric-value" style="font-size:20px">{_esc(value)}</div></div>'
        for label, value in rows
    )
    show(f'<div class="panel"><p class="kicker" style="color:{INK}">{_esc(title)}</p>{body}</div>')


def decision_header(detail: dict) -> None:
    decision_tone = DECISION_TONE.get(detail.get("decision_type") or "", "grey")
    status_tone = STATUS_TONE.get(detail.get("status") or "", "grey")
    thesis_tone = STATUS_TONE.get(detail.get("thesis_status") or "", "grey")
    show(
        '<div class="page-head" style="align-items:flex-start">'
        f'<div><div class="decision-symbol">{_esc(detail.get("symbol"))}</div>'
        f'<p class="decision-sub">{_esc(detail.get("company_name"))}</p></div>'
        "<div>"
        f'{status_badge(str(detail.get("decision_type") or ""), decision_tone)}'
        f'{status_badge(str(detail.get("status") or ""), status_tone)}'
        "</div></div>"
    )
    metric_strip(
        [
            {"value": detail.get("expected_horizon") or "—", "label": "Horizon"},
            {"value": detail.get("expected_upside") or "—", "label": "Upside", "tone": tone_for_signed(str(detail.get("expected_upside") or ""), gains=True)},
            {"value": detail.get("expected_downside") or "—", "label": "Downside", "tone": tone_for_signed(str(detail.get("expected_downside") or ""))},
            {"value": confidence_label(str(detail.get("confidence") or "")), "label": "Confidence"},
        ]
    )
    show(f'<p class="decision-statement">{_rich(detail.get("proposed_action"))}</p>')


def confidence_bar(value: float | None) -> None:
    if value is None:
        empty_state("Confidence was not recorded.")
        return
    width = max(0, min(100, int(round(value * 100))))
    show(
        '<p class="mini-head">Your confidence</p>'
        f'<div class="bar" style="width:180px;height:8px"><span style="width:{width}%"></span></div>'
        f'<p class="meta">{width}% · recorded by you, not by a model</p>'
    )


def decision_narrative(detail: dict) -> None:
    thesis_tone = STATUS_TONE.get(detail.get("thesis_status") or "", "grey")
    section_header("Why")
    show(f'<p class="prose">{_rich(detail.get("reasoning"))}</p>')
    if detail.get("actual_action") and detail.get("actual_action") != "—":
        show(f'<p class="meta">What happened · {_esc(detail.get("actual_action"))}</p>')
    if detail.get("trade"):
        show(f'<p class="meta">Linked fill · {_esc(detail.get("trade"))}. No order was sent from this screen.</p>')
    risk_callout("INVALIDATION", "What would prove this wrong", detail.get("invalidation") or "Not recorded.", "watch")
    if detail.get("thesis_title") and detail.get("thesis_title") != "—":
        show(
            f'<p class="meta" style="margin-top:8px">Thesis · {_esc(detail.get("thesis_title"))} '
            f'{status_badge(str(detail.get("thesis_status") or ""), thesis_tone)}</p>'
        )


def thesis_panel(thesis: dict) -> None:
    tone = STATUS_TONE.get(thesis.get("status") or "", "grey")
    thesis_label = f'{thesis.get("status") or ""} THESIS'.strip()
    show(
        '<div class="panel" style="border-top:3px solid #ACA0C4">'
        f'<div class="decision-symbol" style="font-size:28px">{_esc(thesis.get("symbol"))}</div>'
        f'<div style="margin:8px 0">{status_badge(thesis_label, tone)}</div>'
        f'<p class="prose" style="font-size:18px;font-weight:640">{_rich(thesis.get("title"))}</p>'
        '<p class="mini-head">Core thesis</p>'
        f'<p class="prose">{_rich(thesis.get("summary"))}</p>'
        f'<p class="meta">Horizon · {_esc(thesis.get("horizon"))}</p>'
        '<p class="mini-head">Base case</p>'
        f'<p class="prose">{_rich(thesis.get("base"))}</p>'
        '<div class="card-grid two" style="margin-top:12px">'
        '<div class="panel" style="border-top:3px solid #91A88F"><p class="kicker" style="color:#3F5C3E">Bull</p>'
        f'<p class="prose">{_rich(thesis.get("bull"))}</p></div>'
        '<div class="panel" style="border-top:3px solid #D9897E"><p class="kicker" style="color:#8C4A42">Bear</p>'
        f'<p class="prose">{_rich(thesis.get("bear"))}</p></div>'
        "</div>"
        '<div class="panel" style="border-top:3px solid #ACA0C4;margin-top:12px">'
        '<p class="kicker" style="color:#554C72">Not yet known</p>'
        f'<p class="prose">{_rich(thesis.get("catalysts") or "No open question is stored on this thesis.")}</p>'
        "</div></div>"
    )
    risk_callout("INVALIDATION", "What would end this thesis", thesis.get("invalidation") or "Not recorded.", "watch")


def catalyst_lane(rows: list[dict]) -> None:
    if not rows:
        empty_state("No catalyst is stored.")
        return
    blocks = []
    for row in rows:
        blocks.append(
            '<div class="stack-item">'
            f'{status_badge(str(row.get("status") or "UNKNOWN"), "lavender")}'
            f'<strong class="ticker">{_esc(row.get("symbol"))}</strong>'
            f'<div class="prose">{_esc(row.get("description"))}</div>'
            f'<div class="meta">{_esc(row.get("window"))}</div>'
            "</div>"
        )
    show("".join(blocks))


def placeholder_grid(items: list[tuple[str, str]]) -> None:
    cells = "".join(
        f'<div class="panel"><p class="kicker" style="color:{INK}">{_esc(title)}</p>'
        f'<p class="empty">{_esc(message)}</p></div>'
        for title, message in items
    )
    show(f'<div class="card-grid two">{cells}</div>')


def journal_entries(entries: list[dict]) -> None:
    section_header("Journal")
    if not entries:
        empty_state("No journal entry yet.")
        return
    for entry in entries:
        show(
            '<div class="stack-item">'
            f'<div class="meta">{_esc(entry.get("created_at"))}</div>'
            f'<p class="prose"><strong>Thought.</strong> {_rich(entry.get("thought"))}</p>'
            f'<p class="prose"><strong>Expected.</strong> {_rich(entry.get("expected"))}</p>'
            f'<p class="prose"><strong>Would prove me wrong.</strong> {_rich(entry.get("wrong"))}</p>'
            + (f'<p class="meta">{_esc(entry.get("notes"))}</p>' if entry.get("notes") else "")
            + "</div>"
        )


_PLAIN_CODES = (
    ("CONCENTRATION", "concentration"),
    ("TOTAL_VALUE", "portfolio value"),
    ("WEIGHT_CHANGE", "weight change"),
    ("NEW_POSITION", "new position"),
    ("CLOSED_POSITION", "closed position"),
    ("SCENARIO", "scenario"),
    ("DRAWDOWN", "drawdown"),
)


def _plain_codes(value: object) -> str:
    text = "" if value is None else str(value)
    for code, word in _PLAIN_CODES:
        text = text.replace(code, word)
    return text


def _claim_bits(rows: list, is_claim: bool) -> str:
    pieces = []
    for row in rows:
        if is_claim and isinstance(row, dict):
            kind = str(row.get("claim_type") or "UNKNOWN")
            tone = {"FACT": "blue", "INFERENCE": "apricot", "OPINION": "lavender", "UNKNOWN": "grey"}.get(kind, "grey")
            pieces.append(
                f'<div class="stack-item">{status_badge(kind, tone)}'
                f'<span class="prose">{_esc(_plain_codes(row.get("text")))}</span></div>'
            )
        else:
            pieces.append(f'<div class="stack-item prose">{_esc(_plain_codes(row))}</div>')
    return "".join(pieces)


def ai_agent_panel(role: str, payload: dict | None, meta: str = "") -> None:
    css = AGENT_CLASS.get(role, "brief")
    label = AGENT_LABEL.get(role, role.replace("_", " ").title())
    if not payload:
        show(f'<div class="panel agent {css}"><p class="kicker">{_esc(label)}</p><p class="empty">Not run.</p></div>')
        return
    sections = []
    for title, key, is_claim in _CLAIM_SECTIONS:
        rows = payload.get(key) or []
        if not rows:
            continue
        sections.append(f'<p class="mini-head">{_esc(title)}</p>{_claim_bits(rows, is_claim)}')
    meta_html = f'<p class="meta">{_esc(meta)}</p>' if meta else ""
    confidence = payload.get("confidence")
    confidence_html = f'<p class="meta">Stated confidence {confidence}</p>' if confidence is not None else ""
    show(
        f'<div class="panel agent {css}"><p class="kicker">{_esc(label)}</p>'
        f'<p class="prose">{_rich(payload.get("summary"))}</p>'
        f'{"".join(sections)}{confidence_html}{meta_html}'
        f'<p class="meta">{_esc(payload.get("disclaimer") or DISCLAIMER)}</p></div>'
    )


def brief_block(brief: dict | None) -> None:
    section_header("AI review", "Interpretation of the calculated changes. Not a trade.")
    if not brief:
        empty_state("No AI review is stored for this snapshot.")
        return
    show(
        f'<div class="panel agent brief"><p class="kicker">AI review</p>'
        f'<p class="prose">{_rich(brief.get("summary"))}</p>'
        f'<p class="mini-head">What changed</p>{_claim_bits(brief.get("what_changed") or [], False)}'
        f'<p class="mini-head">Risks</p>{_claim_bits(brief.get("risks") or [], False)}'
        f'<p class="mini-head">Worth researching</p>{_claim_bits(brief.get("opportunities") or [], False)}'
        f'<p class="mini-head">Missing information</p>{_claim_bits(brief.get("unknowns") or [], False)}'
        f'<p class="meta">{_esc(brief.get("disclaimer") or DISCLAIMER)}</p></div>'
    )
    with st.expander("How this review was produced"):
        st.caption(
            f"{_model_label(brief.get('model'))} · analysis {brief.get('analysed_at') or '—'} · "
            f"data {brief.get('data_as_of') or '—'} · thinking {brief.get('thinking_level') or '—'} · "
            f"estimated cost ${brief.get('cost')}. Cost is an estimate, not an invoice."
        )


def _model_label(model: object) -> str:
    if model == "gemini-3.8-flash":
        return "Gemini 3.8 Flash"
    return "—" if not model else str(model)


def portfolio_pulse(view: dict, *, market_pnl: str | None = None) -> None:
    summary = summary_map(view["summary"])
    if market_pnl is None:
        today = {"value": "—", "label": "Today", "hint": "Needs current prices and previous closes", "tone": ""}
    else:
        today = {"value": market_pnl, "label": "Today", "hint": "Market P&L versus the previous close", "tone": tone_for_signed(market_pnl, gains=True)}
    hero_number(summary.get("Portfolio value", "—"), "Total portfolio")
    metric_strip(
        [
            today,
            {"value": summary.get("Cash", "—"), "label": "Cash", "hint": ""},
            {"value": summary.get("Invested", "—"), "label": "Invested", "hint": ""},
        ]
    )


def render_claims(payload: dict | None) -> None:
    ai_agent_panel("SYNTHESIS", payload)

