import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st

from app.dashboard.views.ai_review import render as render_ai
from app.dashboard.views.decisions import render as render_decisions
from app.dashboard.views.holdings import render as render_holdings
from app.dashboard.views.research_lab import render as render_research
from app.dashboard.views.risk_lab import render as render_risk
from app.dashboard.views.stock_detail import render as render_holding
from app.dashboard.views.today import render as render_today
from app.dashboard.system_status import render_system_status
from app.dashboard.ui import inject_css

st.set_page_config(page_title="Investment Intelligence", layout="wide")
inject_css()
st.sidebar.markdown(
    '<p class="brand-kicker">Personal</p><p class="brand-title">Investment intelligence</p>',
    unsafe_allow_html=True,
)
render_system_status()
st.html(
    """
    <button type="button" class="trd-menu">Menu</button>
    <script>
    (function () {
      if (window.__trdNavToggle) return;
      window.__trdNavToggle = true;
      function place() {
        var sidebar = document.querySelector('[data-testid="stSidebar"]');
        if (!sidebar) return;
        sidebar.getAnimations().forEach(function (animation) { animation.cancel(); });
        if (window.innerWidth > 900) {
          sidebar.style.removeProperty("transform");
          return;
        }
        var open = document.body.classList.contains("trd-nav-open");
        sidebar.style.setProperty("transform", open ? "none" : "translateX(-105%)", "important");
      }
      document.addEventListener("click", function (event) {
        var menu = event.target.closest(".trd-menu");
        if (menu) {
          event.preventDefault();
          document.body.classList.toggle("trd-nav-open");
          place();
          return;
        }
        if (!document.body.classList.contains("trd-nav-open")) return;
        var sidebar = document.querySelector('[data-testid="stSidebar"]');
        if (sidebar && sidebar.contains(event.target)) {
          if (event.target.closest("a")) document.body.classList.remove("trd-nav-open");
          place();
          return;
        }
        document.body.classList.remove("trd-nav-open");
        place();
      });
      window.addEventListener("resize", place);
      var tries = 0;
      function kick() {
        place();
        if (document.querySelector('[data-testid="stSidebar"]') || ++tries > 40) return;
        requestAnimationFrame(kick);
      }
      kick();
      setTimeout(place, 400);
    })();
    </script>
    """,
    unsafe_allow_javascript=True,
)
holding = st.Page(render_holding, title="Holding", url_path="holding", visibility="hidden")
st.session_state["holding_page"] = holding
navigation = st.navigation(
    [
        st.Page(render_today, title="Today", url_path="today", default=True),
        st.Page(render_holdings, title="Holdings", url_path="holdings"),
        st.Page(render_decisions, title="Decisions", url_path="decisions"),
        st.Page(render_risk, title="Risk lab", url_path="risk"),
        st.Page(render_research, title="Research lab", url_path="research"),
        st.Page(render_ai, title="AI review", url_path="ai"),
        holding,
    ]
)
navigation.run()
