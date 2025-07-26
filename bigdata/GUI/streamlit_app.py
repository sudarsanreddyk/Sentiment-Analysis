import streamlit as st
from streamlit_autorefresh import st_autorefresh
import requests
import pandas as pd

# ── Config ───────────────────────────────────────────────
API_BASE = "http://ml_api:8000"      # inside Docker network
# For local dev (run API on host): API_BASE = "http://localhost:8000"

# ── Page Setup ────────────────────────────────────────────
st.set_page_config(page_title="Keyword Sentiment Tracker", layout="centered")
st.title("Keyword Sentiment Tracker")

# ── Keyword Input ─────────────────────────────────────────
term1 = st.text_input("Keyword 1", "trump")
term2 = st.text_input("Keyword 2", "harris")

# ── Analyse Button ────────────────────────────────────────
if st.button("Analyse", type="primary"):

    records = []                              # collect rows for DataFrame

    for kw in (term1, term2):
        try:
            resp = requests.get(
                f"{API_BASE}/aggregate",
                params={"keyword": kw},
                timeout=5,
            )
            resp.raise_for_status()
            js = resp.json()

            records.append(
                {
                    "keyword": kw,
                    "percent_positive": js["percent_positive"] or 0.0,
                    "num_samples_total": js["num_samples_total"],
                }
            )

        except Exception as err:
            st.error(f"⚠️ {kw}: {err}")
            records.append(
                {"keyword": kw, "percent_positive": 0.0, "num_samples_total": 0}
            )

    # ── Display Table ──────────────────────────────────────
    df = pd.DataFrame(records)
    st.write(df)

    # ── Display Bar Chart ──────────────────────────────────
    st.bar_chart(df.set_index("keyword"))

# ── Auto-refresh every 60 s (optional)─────────────────────
st_autorefresh(interval=60_000)
