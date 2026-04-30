import os

import streamlit as st
from dotenv import load_dotenv


def require_login():
    load_dotenv()
    app_password = (os.getenv("APP_PASSWORD") or "").strip()

    # If APP_PASSWORD is not configured, auth gate is disabled.
    if not app_password:
        return

    st.session_state.setdefault("authenticated", False)

    if st.session_state.authenticated:
        return

    st.title("Management Login")
    password = st.text_input("Password", type="password")

    if st.button("Enter"):
        if password == app_password:
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("Incorrect password")

    st.stop()
