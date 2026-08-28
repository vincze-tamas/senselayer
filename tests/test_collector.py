import importlib.util
from pathlib import Path

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
