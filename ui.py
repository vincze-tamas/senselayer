import numpy as np
import streamlit as st

from sim import Muse2Simulator, load_config

st.set_page_config(page_title="BCI Simulator", page_icon="🧠", layout="wide")

st.autorefresh(interval=1500, key="bci_sim_refresh")

LABELS = {
    "delta": "Delta",
    "theta": "Theta",
    "alpha": "Alpha",
    "beta": "Beta",
    "gamma": "Gamma",
}


def band_norms(summary):
    vals = np.array([summary.delta, summary.theta, summary.alpha, summary.beta, summary.gamma], dtype=float)
    vals = vals / (vals.max() + 1e-6)
    return dict(zip(LABELS.keys(), np.clip(vals, 0, 1)))


def brain_svg(intensities, colors, state_label):
    cx, cy = 300, 220
    widths = {"delta": 210, "theta": 240, "alpha": 270, "beta": 300, "gamma": 330}
    opacities = {k: 0.12 + 0.62 * v for k, v in intensities.items()}
    stroke_widths = {k: 3 + 10 * v for k, v in intensities.items()}

    rings = []
    for band in ["delta", "theta", "alpha", "beta", "gamma"]:
        r = widths[band] / 2
        rings.append(
            f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{colors[band]}" stroke-width="{stroke_widths[band]:.1f}" stroke-opacity="{opacities[band]:.2f}" stroke-linecap="round">'
            f'<animate attributeName="r" values="{r-8};{r+10};{r-8}" dur="2.3s" repeatCount="indefinite"/>'
            f'<animate attributeName="stroke-opacity" values="{opacities[band]:.2f};0.95;{opacities[band]:.2f}" dur="1.8s" repeatCount="indefinite"/></circle>'
        )

    state_color = "#22c55e" if state_label == "Focus" else "#f59e0b"
    brain = f'''
    <svg viewBox="0 0 600 440" width="100%" height="400" preserveAspectRatio="xMidYMid meet" xmlns="http://www.w3.org/2000/svg">
      <defs>
        <radialGradient id="glow" cx="50%" cy="45%" r="55%">
          <stop offset="0%" stop-color="#ffffff" stop-opacity="0.25"/>
          <stop offset="100%" stop-color="#0f172a" stop-opacity="0.0"/>
        </radialGradient>
        <filter id="shadow" x="-30%" y="-30%" width="160%" height="160%">
          <feDropShadow dx="0" dy="0" stdDeviation="10" flood-color="#60a5fa" flood-opacity="0.35"/>
        </filter>
      </defs>
      <rect x="0" y="0" width="600" height="440" rx="32" fill="#08111f"/>
      <circle cx="300" cy="220" r="175" fill="url(#glow)"/>
      {''.join(rings)}
      <g filter="url(#shadow)" stroke="#e2e8f0" stroke-width="6" fill="#111827">
        <path d="M245 128c-28 0-50 22-50 50 0 12 4 23 11 32-12 12-18 28-18 45 0 33 26 60 59 60h18c9 18 28 30 49 30s40-12 49-30h18c33 0 59-27 59-60 0-17-6-33-18-45 7-9 11-20 11-32 0-28-22-50-50-50-15 0-29 7-38 18-11-14-28-23-47-23-19 0-36 9-47 23-9-11-23-18-38-18z"/>
        <path d="M272 144c-18 6-32 23-32 43 0 9 2 16 6 23" fill="none" stroke="#94a3b8" stroke-linecap="round"/>
        <path d="M328 144c18 6 32 23 32 43 0 9-2 16-6 23" fill="none" stroke="#94a3b8" stroke-linecap="round"/>
        <path d="M240 231c16-18 39-28 60-28s44 10 60 28" fill="none" stroke="#475569" stroke-linecap="round"/>
        <path d="M268 255c10 8 22 12 32 12s22-4 32-12" fill="none" stroke="#64748b" stroke-linecap="round"/>
      </g>
      <text x="300" y="78" text-anchor="middle" font-size="26" fill="#e2e8f0" font-family="Inter, system-ui, sans-serif">BCI Simulator</text>
      <text x="300" y="372" text-anchor="middle" font-size="16" fill="#cbd5e1" font-family="Inter, system-ui, sans-serif">Current state: {state_label}</text>
      <circle cx="300" cy="118" r="15" fill="{state_color}" fill-opacity="0.14"/>
      <circle cx="300" cy="118" r="7" fill="{state_color}"/>
    </svg>
    '''
    return brain


st.markdown(
    """
    <style>
    .stApp {
        background: radial-gradient(circle at top, #111827 0%, #050816 60%, #02040a 100%);
        color: #e5e7eb;
    }
    .card {
        background: rgba(15, 23, 42, 0.82);
        border: 1px solid rgba(148, 163, 184, 0.2);
        border-radius: 18px;
        padding: 18px;
        box-shadow: 0 0 40px rgba(56, 189, 248, 0.08);
    }
    .state-stack {
        width: 100%;
        background: rgba(30, 41, 59, 0.9);
        border: 1px solid rgba(148, 163, 184, 0.18);
        border-radius: 999px;
        overflow: hidden;
        height: 22px;
        display: flex;
        box-shadow: inset 0 0 0 1px rgba(15, 23, 42, 0.35);
    }
    .state-focus-bar {
        background: linear-gradient(90deg, #22c55e, #16a34a);
        height: 100%;
        display: flex;
        align-items: center;
        justify-content: flex-start;
        padding-left: 10px;
        color: #eafff0;
        font-size: 0.78rem;
        font-weight: 700;
        white-space: nowrap;
    }
    .state-noise-bar {
        background: linear-gradient(90deg, #ef4444, #b91c1c);
        height: 100%;
        display: flex;
        align-items: center;
        justify-content: flex-end;
        padding-right: 10px;
        color: #fff1f1;
        font-size: 0.78rem;
        font-weight: 700;
        white-space: nowrap;
    }
    .state-legend {
        display: flex;
        justify-content: space-between;
        margin-top: 0.35rem;
        font-size: 0.86rem;
        color: #cbd5e1;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("🧠 BCI Simulator")
st.caption("Synthetic Muse 2-style EEG activity with animated visual feedback, focus detection, and artifact/noise awareness.")

config = load_config()
sim = Muse2Simulator(config)
frame, summary = sim.step()
intensities = band_norms(summary)
colors = config["colors"]

focus_raw = max(float(summary.focus_score), 0.0)
noise_raw = max(float(summary.noise_score), 0.0)
state_total = focus_raw + noise_raw
if state_total <= 1e-9:
    focus_pct = 50.0
    noise_pct = 50.0
else:
    focus_pct = 100.0 * focus_raw / state_total
    noise_pct = 100.0 * noise_raw / state_total

left, right = st.columns([1.35, 1])

with left:
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown(brain_svg(intensities, colors, summary.state_label), unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

with right:
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.metric("Current State", summary.state_label)
    peak_band = max(("delta", "theta", "alpha", "beta", "gamma"), key=lambda band: intensities[band]).title()
    st.metric("Peak Band", peak_band)

    st.write("State balance")
    st.markdown(
        f'''
        <div class="state-stack" role="img" aria-label="Focus {focus_pct:.0f} percent and Noise {noise_pct:.0f} percent">
          <div class="state-focus-bar" style="width: {focus_pct:.1f}%;">Focus {focus_pct:.0f}%</div>
          <div class="state-noise-bar" style="width: {noise_pct:.1f}%;">Noise {noise_pct:.0f}%</div>
        </div>
        <div class="state-legend">
          <span>Focus</span>
          <span>Noise</span>
        </div>
        ''',
        unsafe_allow_html=True,
    )

    st.write("Band emphasis")
    for band in ["delta", "theta", "alpha", "beta", "gamma"]:
        floored = max(float(intensities[band]), 0.08)
        st.progress(floored)
        st.caption(f"{LABELS[band]} \u2022 intensity {intensities[band]:.2f}")
    st.markdown('</div>', unsafe_allow_html=True)

st.markdown('<div class="card">', unsafe_allow_html=True)
st.subheader("Live synthetic EEG window")
st.line_chart(frame.set_index("time")[["raw", "delta", "theta", "alpha", "beta", "gamma"]])
st.markdown('</div>', unsafe_allow_html=True)

with st.expander("Configuration"):
    st.json(config)
