import importlib.util
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "muse2_edge_collector.py"
SPEC = importlib.util.spec_from_file_location("collector", SCRIPT)
collector = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(collector)


def test_normalize_bands_sums_to_one():
    result = collector.normalize_bands({
        "delta": 1, "theta": 2, "alpha": 3, "beta": 2, "gamma": 2,
    })
    assert sum(result.values()) == pytest.approx(1.0)
    assert result["alpha"] == pytest.approx(0.3)


def test_alpha_sine_is_alpha_dominant():
    fs = 256
    seconds = 2
    t = np.arange(fs * seconds) / fs
    channel = np.sin(2 * np.pi * 10 * t)
    window = np.column_stack([channel, channel * 0.8, channel * 1.2, channel])
    powers = collector.normalize_bands(collector.band_powers(window, fs))
    assert powers["alpha"] > 0.98


def test_simulated_bands_are_valid():
    for index in range(100):
        bands = collector.simulated_bands(index)
        assert sum(bands.values()) == pytest.approx(1.0)
        assert all(0 <= value <= 1 for value in bands.values())


def test_live_payload_includes_analyzed_quality(monkeypatch):
    window = np.arange(16, dtype=float).reshape(4, 4)
    bands = {
        "delta": 0.1,
        "theta": 0.2,
        "alpha": 0.3,
        "beta": 0.25,
        "gamma": 0.15,
    }
    result = SimpleNamespace(
        score=0.625,
        label="marginal",
        channel_quality={"TP9": 0.5, "AF7": 0.6, "AF8": 0.7, "TP10": 0.7},
        artifact_flags=("high_frequency_noise",),
    )
    analyze_calls = []
    monkeypatch.setattr(collector, "band_powers", lambda actual, fs: bands)
    monkeypatch.setattr(collector, "normalize_bands", lambda actual: actual)
    monkeypatch.setattr(
        collector,
        "analyze_quality",
        lambda actual, sample_rate: analyze_calls.append((actual, sample_rate)) or result,
    )

    payload = collector.live_payload(window, "muse2-edge-win11", 256, timestamp=123.5)

    assert len(analyze_calls) == 1
    assert np.array_equal(analyze_calls[0][0], window)
    assert analyze_calls[0][1] == 256
    assert payload == {
        "timestamp": 123.5,
        "source": "muse2-edge-win11",
        **bands,
        "signal_quality": 0.625,
        "quality_label": "marginal",
        "channel_quality": {"TP9": 0.5, "AF7": 0.6, "AF8": 0.7, "TP10": 0.7},
        "artifact_flags": ["high_frequency_noise"],
    }


def test_simulation_payload_has_deterministic_explicit_quality():
    args = Namespace(source_name="muse2-edge-win11")

    payload = collector.simulation_payload(args, 0, timestamp=456.0)

    assert payload == {
        "timestamp": 456.0,
        "source": "muse2-edge-win11-sim",
        **collector.simulated_bands(0),
        "signal_quality": 1.0,
        "quality_label": "good",
        "channel_quality": {"TP9": 1.0, "AF7": 1.0, "AF8": 1.0, "TP10": 1.0},
        "artifact_flags": [],
    }
