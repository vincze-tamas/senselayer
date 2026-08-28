import os
import sqlite3
import time
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

from pipeline.processor import process_sample
from sim import Muse2Simulator, load_config
from sources.live_source import LiveFileSource

st.set_page_config(page_title="BCI Simulator", page_icon="🧠", layout="wide")

LABELS = {"delta": "Delta", "theta": "Theta", "alpha": "Alpha", "beta": "Beta", "gamma": "Gamma"}
DB_PATH = Path(os.environ.get("SENSELAYER_DATA_DIR", "data")) / "history.db"


def band_norms(values):
    vals = np.array([values[k] for k in ["delta", "theta", "alpha", "beta", "gamma"]], dtype=float)
    vals = vals / (vals.max() + 1e-6)
    return dict(zip(LABELS.keys(), np.clip(vals, 0, 1)))


def read_history(limit=600):
    if not DB_PATH.exists():
        return pd.DataFrame()
    conn = sqlite3.connect(DB_PATH)
    q = """
    SELECT received_at, delta, theta, alpha, beta, gamma
    FROM samples ORDER BY id DESC LIMIT ?
    """
    df = pd.read_sql_query(q, conn, params=(limit,))
    conn.close()
    if df.empty:
        return df
    df = df.iloc[::-1].copy()
    df["ts"] = pd.to_datetime(df["received_at"], unit="s")
    return df


st.title("🧠 BCI Live + History")
config = load_config()
source = LiveFileSource()
sample = source.next()
mode = "live"

if sample is None:
    mode = "sim"
    sim = Muse2Simulator(config)
    _frame, summary = sim.step()
    sample = {"delta": summary.delta, "theta": summary.theta, "alpha": summary.alpha, "beta": summary.beta, "gamma": summary.gamma}

state = process_sample(sample)
intensities = band_norms(sample)

focus_raw = max(float(state.focus_score), 0.0)
noise_raw = max(float(state.noise_score), 0.0)
state_total = focus_raw + noise_raw
focus_pct = 50.0 if state_total <= 1e-9 else 100.0 * focus_raw / state_total
noise_pct = 50.0 if state_total <= 1e-9 else 100.0 * noise_raw / state_total

c1, c2, c3 = st.columns(3)
c1.metric("State", state.state_label)
c2.metric("Source", mode)
c3.metric("Focus %", f"{focus_pct:.0f}")

st.subheader("Current bands")
for band in ["delta", "theta", "alpha", "beta", "gamma"]:
    st.progress(float(max(intensities[band], 0.05)))
    st.caption(f"{LABELS[band]} • {sample[band]:.4f}")

st.subheader("History (last ~600 samples)")
h = read_history(600)
if h.empty:
    st.info("Még nincs history adat.")
else:
    st.line_chart(h.set_index("ts")[["delta", "theta", "alpha", "beta", "gamma"]])
    st.caption(f"Pontok: {len(h)} | from {h['ts'].iloc[0]} to {h['ts'].iloc[-1]}")

st.caption("Auto refresh 2s")
time.sleep(2)
if hasattr(st, "rerun"):
    st.rerun()
else:
    st.experimental_rerun()
