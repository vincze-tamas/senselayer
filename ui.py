import os
import sqlite3
import time
from numbers import Real
from pathlib import Path
from typing import Any, Callable, Mapping

import numpy as np
import pandas as pd
import streamlit as st

from pipeline.processor import process_sample
from pipeline.eeg_quality import (
    BLOCKING_ARTIFACTS,
    DEFAULT_CHANNEL_NAMES,
    GOOD_SCORE_THRESHOLD,
    MARGINAL_SCORE_THRESHOLD,
)
from sim import Muse2Simulator, load_config
from sources.live_source import LiveFileSource

LABELS = {"delta": "Delta", "theta": "Theta", "alpha": "Alpha", "beta": "Beta", "gamma": "Gamma"}
DB_PATH = Path(os.environ.get("SENSELAYER_DATA_DIR", "data")) / "history.db"
QUALITY_STYLES = {
    "good": ("Good", "#2e7d32"),
    "marginal": ("Marginal", "#ed6c02"),
    "bad": ("Bad", "#d32f2f"),
    "unknown": ("Unknown", "#616161"),
}
SIMULATION_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
SUPPRESSED_TEXT = "Suppressed: insufficient signal quality"
BANDS = tuple(LABELS)
KNOWN_ARTIFACT_FLAGS = frozenset(
    {
        "non_finite_samples",
        "flatline",
        "extreme_amplitude",
        "abrupt_steps",
        "high_frequency_noise",
        "channel_outlier",
    }
)


def normalized_quality_label(label: object) -> str:
    normalized = str(label).strip().lower() if label is not None else "unknown"
    return normalized if normalized in QUALITY_STYLES else "unknown"


def quality_presentation(label: object) -> tuple[str, str]:
    return QUALITY_STYLES[normalized_quality_label(label)]


def should_show_derived_state(label: object) -> bool:
    return normalized_quality_label(label) in {"good", "marginal"}


def strict_float(value: object) -> float | None:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Real):
        return None
    converted = float(value)
    return converted if np.isfinite(converted) else None


def has_valid_band_values(sample: Mapping[str, object]) -> bool:
    if any(band not in sample for band in BANDS):
        return False
    values = [strict_float(sample[band]) for band in BANDS]
    return all(value is not None and value >= 0.0 for value in values)


def quality_label_for_sample(sample: Mapping[str, object]) -> str:
    label = normalized_quality_label(sample.get("quality_label"))
    if label == "unknown":
        return label

    score = strict_float(sample.get("signal_quality"))
    if score is None or not 0.0 <= score <= 1.0:
        return "unknown"

    channel_quality = sample.get("channel_quality")
    if not isinstance(channel_quality, dict) or set(channel_quality) != set(DEFAULT_CHANNEL_NAMES):
        return "unknown"
    channel_scores = [strict_float(value) for value in channel_quality.values()]
    if not all(value is not None and 0.0 <= value <= 1.0 for value in channel_scores):
        return "unknown"
    valid_channel_scores = [value for value in channel_scores if value is not None]
    if not np.isclose(score, float(np.mean(valid_channel_scores)), rtol=0.0, atol=1e-6):
        return "unknown"

    artifact_flags = sample.get("artifact_flags")
    if not isinstance(artifact_flags, (list, tuple)) or not all(
        isinstance(flag, str) for flag in artifact_flags
    ):
        return "unknown"
    if any(flag not in KNOWN_ARTIFACT_FLAGS for flag in artifact_flags):
        return "unknown"

    blocking = any(flag in BLOCKING_ARTIFACTS for flag in artifact_flags)
    expected_label = (
        "bad"
        if blocking or score < MARGINAL_SCORE_THRESHOLD
        else "marginal"
        if score < GOOD_SCORE_THRESHOLD
        else "good"
    )
    return label if label == expected_label else "unknown"


def quality_score_text(score: object) -> str:
    value = strict_float(score)
    return f"{value:.0%}" if value is not None and 0.0 <= value <= 1.0 else "n/a"


def channel_quality_text(channel_quality: object) -> str:
    if not isinstance(channel_quality, dict) or not channel_quality:
        return "unavailable"
    formatted = []
    for name, value in channel_quality.items():
        score = strict_float(value)
        if score is None or not 0.0 <= score <= 1.0:
            return "unavailable"
        formatted.append(f"{name}: {score:.0%}")
    return " · ".join(formatted)


def artifact_flags_text(artifact_flags: object) -> str:
    if not isinstance(artifact_flags, (list, tuple)) or not all(
        isinstance(flag, str) for flag in artifact_flags
    ):
        return "unavailable"
    return ", ".join(artifact_flags) if artifact_flags else "none"


def simulation_allowed(environment: Mapping[str, str] | None = None) -> bool:
    source = os.environ if environment is None else environment
    return source.get("SENSELAYER_ALLOW_SIMULATION", "").strip().lower() in SIMULATION_TRUE_VALUES


def select_dashboard_sample(
    live_sample: dict | None,
    config: dict,
    *,
    allow_simulation: bool,
    simulator_factory: Callable[[dict], Any] = Muse2Simulator,
) -> tuple[dict | None, str | None]:
    if live_sample is not None:
        return (live_sample, "live") if has_valid_band_values(live_sample) else (None, None)
    if not allow_simulation:
        return None, None

    simulator = simulator_factory(config)
    _frame, summary = simulator.step()
    return {
        "delta": summary.delta,
        "theta": summary.theta,
        "alpha": summary.alpha,
        "beta": summary.beta,
        "gamma": summary.gamma,
        "signal_quality": 1.0,
        "quality_label": "good",
        "channel_quality": {name: 1.0 for name in DEFAULT_CHANNEL_NAMES},
        "artifact_flags": [],
    }, "sim"


def resolve_dashboard_sample(
    live_sample: dict | None,
    *,
    allow_simulation: bool,
    config_loader: Callable[[], dict] = load_config,
    simulator_factory: Callable[[dict], Any] = Muse2Simulator,
) -> tuple[dict | None, str | None]:
    if live_sample is not None:
        return select_dashboard_sample(
            live_sample,
            {},
            allow_simulation=False,
            simulator_factory=simulator_factory,
        )
    if not allow_simulation:
        return None, None
    return select_dashboard_sample(
        None,
        config_loader(),
        allow_simulation=True,
        simulator_factory=simulator_factory,
    )


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


def render_quality(sample: dict) -> str:
    label = quality_label_for_sample(sample)
    text, color = quality_presentation(label)
    score_text = quality_score_text(sample.get("signal_quality"))

    st.subheader("Signal quality")
    st.markdown(
        f'**Aggregate:** <span style="color:{color};font-weight:700">{text}</span> ({score_text})',
        unsafe_allow_html=True,
    )

    st.caption(f"Channels: {channel_quality_text(sample.get('channel_quality'))}")

    artifact_text = artifact_flags_text(sample.get("artifact_flags"))
    if artifact_text not in {"none", "unavailable"}:
        st.warning("Artifacts: " + artifact_text)
    else:
        st.caption("Artifacts: " + artifact_text)
    return label


def render_current_sample(sample: dict, source_name: str) -> None:
    quality_label = render_quality(sample)
    state = process_sample(sample)
    intensities = band_norms(sample)

    focus_raw = max(float(state.focus_score), 0.0)
    noise_raw = max(float(state.noise_score), 0.0)
    state_total = focus_raw + noise_raw
    focus_pct = 50.0 if state_total <= 1e-9 else 100.0 * focus_raw / state_total

    c1, c2, c3 = st.columns(3)
    if should_show_derived_state(quality_label):
        c1.metric("State", state.state_label)
        c3.metric("Focus %", f"{focus_pct:.0f}")
    else:
        c1.metric("State", SUPPRESSED_TEXT)
        c3.metric("Focus %", SUPPRESSED_TEXT)
    c2.metric("Source", source_name)

    st.subheader("Current bands")
    for band in ["delta", "theta", "alpha", "beta", "gamma"]:
        st.progress(float(max(intensities[band], 0.05)))
        st.caption(f"{LABELS[band]} • {sample[band]:.4f}")


def render_history() -> None:
    st.subheader("History (last ~600 samples)")
    history = read_history(600)
    if history.empty:
        st.info("Még nincs history adat.")
    else:
        st.line_chart(history.set_index("ts")[["delta", "theta", "alpha", "beta", "gamma"]])
        st.caption(
            f"Pontok: {len(history)} | from {history['ts'].iloc[0]} to {history['ts'].iloc[-1]}"
        )


def main() -> None:
    st.set_page_config(page_title="BCI Simulator", page_icon="🧠", layout="wide")
    st.title("🧠 BCI Live + History")
    live_sample = LiveFileSource().next()
    sample, source_name = resolve_dashboard_sample(
        live_sample,
        allow_simulation=simulation_allowed(),
    )

    if sample is None or source_name is None:
        st.error("Live data unavailable")
    else:
        render_current_sample(sample, source_name)

    render_history()
    st.caption("Auto refresh 2s")
    time.sleep(2)
    if hasattr(st, "rerun"):
        st.rerun()
    else:
        st.experimental_rerun()


if __name__ == "__main__":
    main()
