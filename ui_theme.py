"""Visual theme extras for the Streamlit UI."""
from __future__ import annotations

import html

import streamlit as st

from i18n import t

_CSS = """
<style>
  .stApp {
    background:
      radial-gradient(880px 260px at 12% -40px, rgba(79, 70, 229, 0.16), transparent 62%),
      radial-gradient(640px 220px at 96% -30px, rgba(255, 122, 89, 0.14), transparent 58%),
      #f3f5fb;
  }
  header[data-testid="stHeader"] {
    background: transparent;
  }
  [data-testid="stSidebar"] {
    box-shadow: 10px 0 28px rgba(16, 20, 40, 0.08);
  }
  .xd-rail {
    height: 4px;
    margin: 0 0 0.9rem;
    border-radius: 999px;
    background: linear-gradient(90deg, #8b7cff 0%, #ff7a59 58%, #5ec8ff 100%);
  }
  .xd-hero {
    display: flex;
    align-items: center;
    gap: 1rem;
    margin: 0.15rem 0 1.15rem;
    padding: 1rem 1.15rem;
    border-radius: 18px;
    background:
      linear-gradient(135deg, rgba(79, 70, 229, 0.14), rgba(255, 122, 89, 0.08) 48%, rgba(14, 165, 233, 0.1)),
      #ffffff;
    border: 1px solid #e1e6f2;
    box-shadow: 0 10px 28px rgba(28, 34, 51, 0.05);
  }
  .xd-logo {
    width: 58px;
    height: 58px;
    flex: 0 0 58px;
    display: grid;
    place-items: center;
    border-radius: 16px;
    background: linear-gradient(145deg, #5b4ce0 0%, #7c6cff 45%, #ff7a59 100%);
    box-shadow: 0 8px 18px rgba(79, 70, 229, 0.28);
  }
  .xd-kicker {
    margin: 0;
    font-size: 0.72rem;
    font-weight: 600;
    letter-spacing: 0.16em;
    text-transform: uppercase;
    color: #5b4ce0;
  }
  .xd-hero h1 {
    margin: 0.12rem 0 0;
    padding: 0;
    font-size: 1.65rem;
    line-height: 1.12;
    font-weight: 700;
    letter-spacing: -0.03em;
    color: #161b2e;
  }
  .xd-sub {
    margin: 0.28rem 0 0;
    color: #5c657a;
    font-size: 0.95rem;
  }
  [data-testid="stMain"] [data-testid="stMetric"] {
    background: linear-gradient(180deg, #ffffff, #f7f8fd);
    border: 1px solid #e1e6f2;
    border-radius: 14px;
    padding: 0.7rem 0.9rem 0.45rem;
    box-shadow: 0 6px 16px rgba(28, 34, 51, 0.04);
  }
  [data-testid="stMain"] [data-testid="stVerticalBlockBorderWrapper"] {
    border-radius: 16px !important;
    border-color: #e1e6f2 !important;
    background: rgba(255, 255, 255, 0.92);
    box-shadow: 0 8px 22px rgba(28, 34, 51, 0.04);
  }
</style>
"""

_LOGO = """
<svg width="28" height="28" viewBox="0 0 24 24" aria-hidden="true">
  <path d="M8.2 6.4v11.2l9.1-5.6-9.1-5.6z" fill="#fff"/>
</svg>
"""


def inject_theme() -> None:
    st.html(_CSS)
    st.sidebar.html('<div class="xd-rail"></div>')


def render_app_header() -> None:
    title = html.escape(t("hero_title"))
    subtitle = html.escape(t("hero_subtitle"))
    kicker = html.escape(t("hero_kicker"))
    st.html(
        f'<div class="xd-hero">'
        f'<div class="xd-logo">{_LOGO}</div>'
        f"<div>"
        f'<p class="xd-kicker">{kicker}</p>'
        f"<h1>{title}</h1>"
        f'<p class="xd-sub">{subtitle}</p>'
        f"</div></div>"
    )
