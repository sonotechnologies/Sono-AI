"""Shared visual polish on top of .streamlit/config.toml's dark theme:
poster styling, card spacing, and mobile touch-target sizing."""
import streamlit as st

_CSS = """
<style>
/* Rounded, shadowed posters everywhere */
div[data-testid="stImage"] img {
    border-radius: 10px;
    box-shadow: 0 4px 14px rgba(0, 0, 0, 0.45);
}

/* Tighter, card-like feel for bordered containers (feed cards) */
div[data-testid="stVerticalBlockBorderWrapper"] {
    border-radius: 12px;
    padding: 0.25rem;
}

/* Bigger, easier-to-tap buttons on mobile */
.stButton > button {
    border-radius: 8px;
    min-height: 2.6rem;
    font-weight: 600;
}

/* Section headers with an accent underline for a less-bland feel */
h1, h2, h3 {
    letter-spacing: -0.01em;
}
h2 {
    border-bottom: 2px solid #e63946;
    padding-bottom: 0.3rem;
    display: inline-block;
}

/* Poster grid captions: compact, centered, no huge gap under images */
div[data-testid="stImageCaption"] {
    text-align: center;
    font-size: 0.85rem;
}

/* Shrink column gaps a bit on narrow screens so grids fit better */
@media (max-width: 640px) {
    div[data-testid="stHorizontalBlock"] {
        gap: 0.5rem;
    }
}
</style>
"""


def apply_theme():
    st.markdown(_CSS, unsafe_allow_html=True)
