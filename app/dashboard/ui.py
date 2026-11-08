"""Compatibility surface. New pages should import from components."""

from app.dashboard.components import inject_css, page_header, render_claims

DISCLAIMER = "Analysis is not an instruction. You make the decision. Brokers are read-only."


def page_shell(title: str, purpose: str) -> None:
    page_header(title, purpose)


__all__ = ["DISCLAIMER", "inject_css", "page_shell", "render_claims"]
