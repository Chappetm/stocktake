from __future__ import annotations

import streamlit as st

from ui.auth import require_login
from views.view_stocktake import render as render_stocktake


def main() -> None:
    st.set_page_config(page_title="Venue Panel", layout="centered")

    st.markdown(
        """
        <style>
        div[data-testid="stButton"] > button[kind="primary"] {
            background-color: #1f9d55 !important;
            border-color: #1f9d55 !important;
            color: #ffffff !important;
        }
        div[data-testid="stButton"] > button[kind="primary"]:hover {
            background-color: #198754 !important;
            border-color: #198754 !important;
            color: #ffffff !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    require_login()
    render_stocktake()


if __name__ == "__main__":
    main()
