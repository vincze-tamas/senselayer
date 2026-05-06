import numpy as np
import streamlit as st

from sim import Muse2Simulator, load_config

st.set_page_config(page_title="BCI Simulator", page_icon="🧠", layout="wide")

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


def brain_svg(intensities, colors, state_label, focus_score, noise_score):
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
    <svg viewBox="0 0 600 440" width="100%" height="auto" xmlns="http://www.w3.org/2000/svg">
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
      <text x="300" y="372" text-anchor="middle" font-size="16" fill="#cbd5e1" font-family="Inter, system-ui, sans-serif">State {state_label} \u2022 Focus {focus_score:.0%} \u2022 Noise {noise_score:.0%}</text>
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
    .state-focus {
        color: #22c55e;
        font-weight: 800;
        font-size: 1.7rem;
        letter-spacing: 0.03em;
    }
    .state-noise {
        color: #f59e0b;
        font-weight: 800;
        font-size: 1.7rem;
        letter-spacing: 0.03em;
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

left, right = st.columns([1.35, 1])

with left:
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.markdown(brain_svg(intensities, colors, summary.state_label, summary.focus_score, summary.noise_score), unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

with right:
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.metric("Current State", summary.state_label)
    st.metric("Focus Score", f"{summary.focus_score:.2f}")
    st.metric("Noise Score", f"{summary.noise_score:.2f}")
    st.write("Band emphasis")
    for band in ["delta", "theta", "alpha", "beta", "gamma"]:
        st.progress(float(intensities[band]))
        st.caption(f"{LABELS[band]} \u2022 intensity {intensities[band]:.2f}")
    st.markdown('</div>', unsafe_allow_html=True)

st.markdown('<div class="card">', unsafe_allow_html=True)
st.subheader("Live synthetic EEG window")
st.line_chart(frame.set_index("time")[["raw", "delta", "theta", "alpha", "beta", "gamma"]])
st.markdown('</div>', unsafe_allow_html=True)

with st.expander("Configuration"):
    st.json(config)
