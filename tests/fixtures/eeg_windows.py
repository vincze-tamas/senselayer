from __future__ import annotations

import numpy as np

FS = 256
SECONDS = 2
N_SAMPLES = FS * SECONDS
N_CHANNELS = 4
CHANNELS = ("tp9", "af7", "af8", "tp10")


def _base_time() -> np.ndarray:
    return np.arange(N_SAMPLES, dtype=float) / FS


def _stack(channels: list[np.ndarray]) -> np.ndarray:
    return np.column_stack(channels).astype(float, copy=False)


def clean_alpha_10hz() -> np.ndarray:
    t = _base_time()
    base = np.sin(2 * np.pi * 10 * t)
    return _stack([base, 0.98 * base, 1.02 * base, 0.99 * base])


def flatline() -> np.ndarray:
    return np.zeros((N_SAMPLES, N_CHANNELS), dtype=float)


def extreme_amplitude() -> np.ndarray:
    t = _base_time()
    base = 250.0 * np.sin(2 * np.pi * 10 * t)
    return _stack([base, 1.05 * base, 0.95 * base, base])


def abrupt_steps() -> np.ndarray:
    half = N_SAMPLES // 2
    first = np.full(half, -1.0, dtype=float)
    second = np.full(N_SAMPLES - half, 1.0, dtype=float)
    stepped = np.concatenate([first, second])
    return _stack([stepped, stepped * 0.85, stepped * 1.1, stepped])


def high_frequency_contamination() -> np.ndarray:
    t = _base_time()
    base = np.sin(2 * np.pi * 10 * t)
    contam = 0.65 * np.sin(2 * np.pi * 40 * t)
    mixed = base + contam
    return _stack([mixed, 0.95 * mixed, 1.02 * mixed, mixed])


def one_channel_outlier() -> np.ndarray:
    t = _base_time()
    clean = np.sin(2 * np.pi * 10 * t)
    outlier = 20.0 * np.sin(2 * np.pi * 1 * t)
    return _stack([clean, clean, clean, outlier])


def non_finite_samples() -> np.ndarray:
    window = clean_alpha_10hz().copy()
    window[10, 0] = np.nan
    window[42, 2] = np.inf
    return window
