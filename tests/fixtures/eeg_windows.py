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
    base = 500.0 * np.sin(2 * np.pi * 10 * t)
    return _stack([base, 1.05 * base, 0.95 * base, base])


def abrupt_steps() -> np.ndarray:
    half = N_SAMPLES // 2
    stepped = np.concatenate(
        [np.full(half, -250.0), np.full(N_SAMPLES - half, 250.0)]
    )
    return _stack([stepped, stepped * 0.85, stepped * 1.1, stepped])


def muse_scale_good_contact() -> np.ndarray:
    """Deterministic Muse-like microvolt signal with per-channel DC offsets."""
    t = _base_time()
    base = np.sin(2 * np.pi * 10 * t) + 0.25 * np.sin(2 * np.pi * 3 * t)
    offsets = np.asarray([-31.7, -30.8, -42.5, -21.5])
    amplitudes = np.asarray([70.0, 14.0, 20.0, 42.0])
    return offsets + base[:, None] * amplitudes


def muse_scale_tp10_disconnected() -> np.ndarray:
    """Muse-like baseline with a large, noisy disconnected TP10 channel."""
    window = muse_scale_good_contact()
    t = _base_time()
    window[:, 3] = -409.0 + 520.0 * np.sin(2 * np.pi * 40 * t)
    return window


def muse_scale_recovered_contact() -> np.ndarray:
    """Deterministic Muse-like signal based on the measured recovery leg."""
    t = _base_time()
    base = np.sin(2 * np.pi * 10 * t) + 0.15 * np.sin(2 * np.pi * 3 * t)
    offsets = np.asarray([-31.7, -30.8, -42.5, -21.5])
    amplitudes = np.asarray([140.0, 35.0, 70.0, 83.0])
    return offsets + base[:, None] * amplitudes


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
