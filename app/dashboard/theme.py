"""Morandi Candy palette and the single stylesheet for the dashboard."""

INK = "#30343F"
MUTED = "#5C6270"
PAPER = "#F8F7F4"
PAPER_2 = "#F1EFEA"
LINE = "#E3E0DA"

BLUE = "#8297B0"
SAGE = "#91A88F"
CORAL = "#D9897E"
APRICOT = "#E6BE7A"
LAVENDER = "#ACA0C4"
ROSE = "#CDA5A5"
CHART_GREY = "#A9ADB4"

# Darker ink for text that sits on a light page. Pastel fills stay on badges.
BLUE_INK = "#3E5168"
SAGE_INK = "#3F5C3E"
CORAL_INK = "#8C4A42"
APRICOT_INK = "#6E5424"
LAVENDER_INK = "#554C72"
ROSE_INK = "#7A4E4E"

TONES = {
    "blue": (BLUE, BLUE_INK, "#F4F6F8"),
    "sage": (SAGE, SAGE_INK, "#F4F7F3"),
    "coral": (CORAL, CORAL_INK, "#FBF4F3"),
    "apricot": (APRICOT, APRICOT_INK, "#FBF7EE"),
    "lavender": (LAVENDER, LAVENDER_INK, "#F6F4F9"),
    "rose": (ROSE, ROSE_INK, "#F8F3F3"),
    "grey": ("#C5C3BE", MUTED, PAPER_2),
}


def stylesheet() -> str:
    return f"""
    <style>
    .stApp {{
      background: {PAPER};
      color: {INK};
      font-family: "Segoe UI", "Helvetica Neue", sans-serif;
    }}
    .block-container {{
      max-width: 1240px;
      padding-top: 1.35rem;
      padding-bottom: 3rem;
    }}
    [data-testid="stSidebar"] {{
      background: {PAPER_2};
      border-right: 1px solid {LINE};
    }}
    [data-testid="stSidebar"] .brand-kicker {{
      margin: 0.2rem 0 0;
      font-size: 11px;
      letter-spacing: 0.14em;
      text-transform: uppercase;
      color: {MUTED};
    }}
    [data-testid="stSidebar"] .brand-title {{
      margin: 0.15rem 0 0.8rem;
      font-size: 18px;
      font-weight: 680;
      letter-spacing: -0.02em;
      color: {INK};
      line-height: 1.2;
    }}
    [data-testid="stSidebarNav"] a {{
      border-radius: 8px;
      color: {INK};
      font-weight: 560;
    }}
    [data-testid="stSidebarNav"] a[aria-current="page"] {{
      background: #E4EAF1;
      color: {BLUE_INK};
    }}
    .sys-status {{
      margin: 0.35rem 0 0.9rem;
      padding-top: 0.65rem;
      border-top: 1px solid {LINE};
    }}
    .sys-status p {{
      margin: 0.08rem 0;
      font-size: 12px;
      line-height: 1.35;
      color: {MUTED};
    }}
    .sys-status .sys-mode {{
      color: {INK};
      font-weight: 650;
      letter-spacing: 0.03em;
    }}
    [data-testid="stHeader"] {{
      background: transparent;
    }}
    .stButton > button, .stFormSubmitButton > button {{
      background: {BLUE_INK};
      color: {PAPER};
      border: none;
      border-radius: 8px;
      font-weight: 620;
    }}
    .stButton > button:hover, .stFormSubmitButton > button:hover {{
      background: #2F3E51;
      color: {PAPER};
      border: none;
    }}
    [data-testid="stExpander"] {{
      background: {PAPER_2};
      border: 1px solid {LINE};
      border-radius: 12px;
    }}
    [data-testid="stTabs"] [data-baseweb="tab-list"] {{
      gap: 6px;
    }}
    .page-head {{
      display: flex;
      justify-content: space-between;
      align-items: flex-end;
      gap: 16px;
      margin-bottom: 8px;
    }}
    .page-title {{
      margin: 0;
      font-size: 34px;
      line-height: 1.05;
      font-weight: 720;
      letter-spacing: -0.035em;
      color: {INK};
    }}
    .page-purpose, .section-hint, .meta {{
      color: {MUTED};
      font-size: 13px;
      line-height: 1.45;
    }}
    .page-purpose {{ margin: 6px 0 0; }}
    .mock-line {{
      margin: 0 0 18px;
      font-size: 12px;
      color: {APRICOT_INK};
    }}
    .source-banner {{
      margin: 0 0 18px;
    }}
    .source-line {{
      margin: 0;
      font-size: 12px;
      font-weight: 750;
      letter-spacing: 0.04em;
    }}
    .source-line.live {{ color: {SAGE_INK}; }}
    .source-line.mock {{ color: {APRICOT_INK}; }}
    .source-line.stale {{ color: {APRICOT_INK}; }}
    .source-meta {{
      margin: 2px 0 0;
      font-size: 12px;
      color: {MUTED};
    }}
    .section-title {{
      margin: 18px 0 2px;
      font-size: 23px;
      font-weight: 650;
      letter-spacing: -0.02em;
      color: {INK};
    }}
    .section-hint {{ margin: 0 0 12px; }}
    .hero-value {{
      margin: 8px 0 0;
      font-size: 40px;
      line-height: 1;
      font-weight: 720;
      letter-spacing: -0.04em;
      color: {INK};
    }}
    .hero-label {{
      margin: 6px 0 16px;
      font-size: 12px;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: {MUTED};
      font-weight: 650;
    }}
    .metric-strip, .card-grid {{
      display: grid;
      gap: 14px;
      margin: 4px 0 8px;
    }}
    .metric-strip {{ grid-template-columns: repeat(3, minmax(0, 1fr)); }}
    .metric-strip.cols-4 {{ grid-template-columns: repeat(4, minmax(0, 1fr)); }}
    .card-grid {{ grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); }}
    .metric {{
      padding: 2px 0 8px;
    }}
    .metric-value {{
      font-size: 26px;
      font-weight: 700;
      letter-spacing: -0.03em;
      color: {INK};
      line-height: 1.1;
    }}
    .metric-label {{
      margin-top: 4px;
      font-size: 11px;
      letter-spacing: 0.11em;
      text-transform: uppercase;
      color: {MUTED};
      font-weight: 650;
    }}
    .metric-hint {{ margin-top: 3px; font-size: 13px; color: {MUTED}; }}
    .pos {{ color: {SAGE_INK}; }}
    .neg {{ color: {CORAL_INK}; }}
    .callout {{
      border-left: 3px solid {LINE};
      background: {PAPER_2};
      border-radius: 0 12px 12px 0;
      padding: 12px 14px;
      margin: 0 0 10px;
    }}
    .callout.high {{ border-left-color: {CORAL}; background: #FBF6F5; }}
    .callout.watch {{ border-left-color: {APRICOT}; background: #FBF8F1; }}
    .callout.ok {{ border-left-color: {SAGE}; background: #F5F7F4; }}
    .callout.info {{ border-left-color: {LAVENDER}; background: #F7F5F9; }}
    .sev {{
      display: inline-block;
      font-size: 11px;
      font-weight: 750;
      letter-spacing: 0.08em;
      margin-bottom: 4px;
    }}
    .callout.high .sev {{ color: {CORAL_INK}; }}
    .callout.watch .sev {{ color: {APRICOT_INK}; }}
    .callout.ok .sev {{ color: {SAGE_INK}; }}
    .callout.info .sev {{ color: {LAVENDER_INK}; }}
    .callout h3 {{
      margin: 0 0 4px;
      font-size: 18px;
      font-weight: 680;
      letter-spacing: -0.02em;
    }}
    .callout p {{ margin: 0; color: {INK}; font-size: 14.5px; line-height: 1.45; }}
    .badge {{
      display: inline-block;
      padding: 3px 8px;
      border-radius: 999px;
      font-size: 11px;
      font-weight: 720;
      letter-spacing: 0.06em;
      margin-right: 6px;
      vertical-align: middle;
      background: {PAPER_2};
      color: {MUTED};
    }}
    .badge.blue {{ background: #E7EDF3; color: {BLUE_INK}; }}
    .badge.sage {{ background: #E7EFE6; color: {SAGE_INK}; }}
    .badge.coral {{ background: #F6E6E3; color: {CORAL_INK}; }}
    .badge.apricot {{ background: #F6EEDD; color: {APRICOT_INK}; }}
    .badge.lavender {{ background: #EEEAF4; color: {LAVENDER_INK}; }}
    .badge.rose {{ background: #F3E8E8; color: {ROSE_INK}; }}
    .badge.grey {{ background: {PAPER_2}; color: {MUTED}; }}
    .decision-symbol {{
      font-size: 36px;
      font-weight: 740;
      letter-spacing: -0.04em;
      line-height: 1;
    }}
    .decision-sub {{ margin: 6px 0 0; color: {MUTED}; font-size: 13px; }}
    .decision-statement {{
      margin: 14px 0 0;
      font-size: 22px;
      font-weight: 640;
      letter-spacing: -0.02em;
      line-height: 1.3;
    }}
    .prose {{
      font-size: 15.5px;
      line-height: 1.55;
      color: {INK};
      margin: 4px 0 0;
    }}
    .panel {{
      background: {PAPER_2};
      border-radius: 12px;
      padding: 14px 16px 12px;
      margin: 0 0 12px;
    }}
    .panel .kicker {{
      font-size: 11px;
      font-weight: 750;
      letter-spacing: 0.1em;
      text-transform: uppercase;
      margin: 0 0 6px;
    }}
    .agent.analyst .kicker {{ color: {BLUE_INK}; }}
    .agent.skeptic .kicker {{ color: {ROSE_INK}; }}
    .agent.risk .kicker {{ color: {CORAL_INK}; }}
    .agent.synthesis .kicker {{ color: {LAVENDER_INK}; }}
    .agent.brief .kicker {{ color: {LAVENDER_INK}; }}
    .mini-head {{
      margin: 12px 0 4px;
      font-size: 12px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: {MUTED};
      font-weight: 700;
    }}
    .mc-table {{
      width: 100%;
      border-collapse: collapse;
      margin: 4px 0 8px;
    }}
    .mc-table th {{
      text-align: left;
      font-size: 11px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: {MUTED};
      font-weight: 700;
      padding: 8px 8px 8px 0;
      border-bottom: 1px solid {LINE};
    }}
    .mc-table td {{
      padding: 11px 10px 11px 0;
      border-bottom: 1px solid {LINE};
      vertical-align: middle;
      font-size: 15px;
      color: {INK};
    }}
    .ticker {{ font-weight: 740; letter-spacing: 0.01em; font-size: 16px; }}
    .bar {{
      height: 7px;
      width: 112px;
      max-width: 100%;
      background: #E7E4DE;
      border-radius: 99px;
      margin-top: 5px;
      overflow: hidden;
    }}
    .bar > span {{
      display: block;
      height: 100%;
      background: {BLUE};
      border-radius: 99px;
    }}
    .scenario-impact {{
      font-size: 28px;
      font-weight: 730;
      letter-spacing: -0.03em;
      line-height: 1;
    }}
    .scenario-kicker {{
      margin-top: 6px;
      font-size: 11px;
      letter-spacing: 0.1em;
      text-transform: uppercase;
      color: {MUTED};
      font-weight: 700;
    }}
    .scenario-name {{ margin-top: 8px; font-weight: 650; }}
    .empty {{
      color: {MUTED};
      font-size: 14px;
      padding: 8px 0 4px;
    }}
    .stack-item {{
      padding: 10px 0;
      border-bottom: 1px solid {LINE};
    }}
    .stack-item strong {{ font-weight: 700; }}
    @media (max-width: 900px) {{
      .metric-strip, .metric-strip.cols-4 {{ grid-template-columns: 1fr; }}
      .page-head {{ flex-direction: column; align-items: flex-start; }}
      .hero-value {{ font-size: 34px; }}
      .page-title {{ font-size: 30px; }}
    }}
    </style>
    """
