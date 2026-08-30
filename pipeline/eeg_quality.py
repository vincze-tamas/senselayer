"""Pure, deterministic quality analysis for short EEG windows.

The checks are engineering heuristics for signal integrity. They are not an
impedance measurement and must not be interpreted as medical diagnostics.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

DEFAULT_CHANNEL_NAMES = ("TP9", "AF7", "AF8", "TP10")

GOOD_SCORE_THRESHOLD = 0.75
MARGINAL_SCORE_THRESHOLD = 0.45
FLATLINE_STD_THRESHOLD = 1e-6
# Muse LSL EEG samples are expressed in microvolts and retain a channel-specific
# DC offset. Threshold amplitude only after robust centering.
EXTREME_AMPLITUDE_THRESHOLD = 250.0
EXTREME_RATIO_THRESHOLD = 0.05
ABRUPT_STEP_THRESHOLD = 180.0
ABRUPT_STEP_RATIO_THRESHOLD = 0.01
SEVERE_ABRUPT_STEP_THRESHOLD = 300.0
HIGH_FREQUENCY_LOW_HZ = 30.0
HIGH_FREQUENCY_HIGH_HZ = 45.0
HIGH_FREQUENCY_RATIO_THRESHOLD = 0.20
CHANNEL_OUTLIER_RATIO_THRESHOLD = 4.0
CHANNEL_AGREEMENT_THRESHOLD = 0.85

BLOCKING_ARTIFACTS = frozenset(
    {
        "non_finite_samples",
        "flatline",
        "extreme_amplitude",
        "abrupt_steps",
        "high_frequency_noise",
        "channel_outlier",
    }
)


@dataclass(frozen=True)
class QualityResult:
    score: float
    label: str
    channel_quality: dict[str, float]
    artifact_flags: tuple[str, ...]


def _clamp01(value: float) -> float:
    if not np.isfinite(value):
        return 0.0
    return float(np.clip(value, 0.0, 1.0))


def _finite_score(channel: np.ndarray) -> float:
    return _clamp01(float(np.mean(np.isfinite(channel))))


def _finite_values(channel: np.ndarray) -> np.ndarray:
    return channel[np.isfinite(channel)]


def _stable_std(values: np.ndarray) -> float:
    scale = float(np.max(np.abs(values)))
    if scale == 0.0:
        return 0.0
    return scale * float(np.std(values / scale))


def _flatline_score(channel: np.ndarray) -> float:
    finite = _finite_values(channel)
    if finite.size == 0:
        return 0.0
    return _clamp01(_stable_std(finite) / FLATLINE_STD_THRESHOLD)


def _is_flatline(channel: np.ndarray) -> bool:
    finite = _finite_values(channel)
    return finite.size == 0 or _stable_std(finite) <= FLATLINE_STD_THRESHOLD


def _extreme_amplitude_ratio(channel: np.ndarray) -> float:
    finite = _finite_values(channel)
    if finite.size == 0:
        return 1.0
    centered = finite - np.median(finite)
    return _clamp01(float(np.mean(np.abs(centered) > EXTREME_AMPLITUDE_THRESHOLD)))


def _abrupt_step_ratio(channel: np.ndarray) -> float:
    adjacent = np.isfinite(channel[:-1]) & np.isfinite(channel[1:])
    if not np.any(adjacent):
        return 1.0
    differences = np.abs(np.diff(channel)[adjacent])
    return _clamp01(float(np.mean(differences > ABRUPT_STEP_THRESHOLD)))


def _maximum_step(channel: np.ndarray) -> float:
    adjacent = np.isfinite(channel[:-1]) & np.isfinite(channel[1:])
    if not np.any(adjacent):
        return 0.0
    return float(np.max(np.abs(np.diff(channel)[adjacent])))


def _has_abrupt_step(channel: np.ndarray) -> bool:
    return (
        _maximum_step(channel) > SEVERE_ABRUPT_STEP_THRESHOLD
        or _abrupt_step_ratio(channel) >= ABRUPT_STEP_RATIO_THRESHOLD
    )


def _high_frequency_ratio(channel: np.ndarray, sample_rate: int) -> float:
    if not np.all(np.isfinite(channel)):
        return 0.0

    scale = float(np.max(np.abs(channel)))
    if scale <= np.finfo(float).eps:
        return 0.0
    centered = channel / scale
    centered = centered - np.mean(centered)
    power = np.abs(np.fft.rfft(centered)) ** 2
    frequencies = np.fft.rfftfreq(centered.size, d=1.0 / sample_rate)
    usable = (frequencies > 0.0) & (frequencies <= HIGH_FREQUENCY_HIGH_HZ)
    total_power = float(np.sum(power[usable]))
    if total_power <= np.finfo(float).eps:
        return 0.0

    high_frequency = (frequencies >= HIGH_FREQUENCY_LOW_HZ) & (
        frequencies <= HIGH_FREQUENCY_HIGH_HZ
    )
    return _clamp01(float(np.sum(power[high_frequency])) / total_power)


def _channel_rms(channel: np.ndarray) -> float:
    finite = _finite_values(channel)
    if finite.size == 0:
        return 0.0
    scale = float(np.max(np.abs(finite)))
    if scale == 0.0:
        return 0.0
    return scale * float(np.sqrt(np.mean(np.square(finite / scale))))


def _amplitude_outlier_channels(window: np.ndarray) -> set[int]:
    rms = np.asarray([_channel_rms(window[:, index]) for index in range(window.shape[1])])
    positive = rms[rms > FLATLINE_STD_THRESHOLD]
    if positive.size < 2:
        return set()

    reference = float(np.median(positive))
    if reference <= FLATLINE_STD_THRESHOLD:
        return set()
    return {
        index
        for index, value in enumerate(rms)
        if value > reference * CHANNEL_OUTLIER_RATIO_THRESHOLD
    }


def _channel_correlation(left: np.ndarray, right: np.ndarray) -> float:
    finite = np.isfinite(left) & np.isfinite(right)
    if np.count_nonzero(finite) < 2:
        return 0.0
    left_finite = left[finite]
    right_finite = right[finite]
    left_scale = float(np.max(np.abs(left_finite)))
    right_scale = float(np.max(np.abs(right_finite)))
    if left_scale == 0.0 or right_scale == 0.0:
        return 0.0
    left_centered = left_finite / left_scale
    right_centered = right_finite / right_scale
    left_centered = left_centered - np.mean(left_centered)
    right_centered = right_centered - np.mean(right_centered)
    denominator = float(np.linalg.norm(left_centered) * np.linalg.norm(right_centered))
    if denominator <= np.finfo(float).eps:
        return 0.0
    return _clamp01(abs(float(np.dot(left_centered, right_centered))) / denominator)


def _agreement_outlier_channels(window: np.ndarray) -> set[int]:
    if window.shape[1] < 3:
        return set()

    outliers: set[int] = set()
    for index in range(window.shape[1]):
        peers = [peer for peer in range(window.shape[1]) if peer != index]
        candidate_agreement = float(
            np.median(
                [
                    _channel_correlation(window[:, index], window[:, peer])
                    for peer in peers
                ]
            )
        )
        peer_agreement = [
            _channel_correlation(window[:, peer], window[:, other])
            for peer_index, peer in enumerate(peers)
            for other in peers[peer_index + 1 :]
        ]
        consensus = float(np.median(peer_agreement)) >= CHANNEL_AGREEMENT_THRESHOLD
        if consensus and candidate_agreement < CHANNEL_AGREEMENT_THRESHOLD:
            outliers.add(index)
    return outliers


def _outlier_channels(window: np.ndarray) -> set[int]:
    return _amplitude_outlier_channels(window) | _agreement_outlier_channels(window)


def _channel_score(channel: np.ndarray, sample_rate: int, is_outlier: bool) -> float:
    finite = _finite_score(channel)
    flatline = _flatline_score(channel)
    extreme = _extreme_amplitude_ratio(channel)
    abrupt = _abrupt_step_ratio(channel)
    high_frequency = _high_frequency_ratio(channel, sample_rate)
    abrupt_score = (
        0.0
        if _maximum_step(channel) > SEVERE_ABRUPT_STEP_THRESHOLD
        else _clamp01(1.0 - abrupt / ABRUPT_STEP_RATIO_THRESHOLD)
    )

    component_scores = (
        finite,
        flatline,
        _clamp01(1.0 - extreme / EXTREME_RATIO_THRESHOLD),
        abrupt_score,
        _clamp01(1.0 - high_frequency / HIGH_FREQUENCY_RATIO_THRESHOLD),
        0.0 if is_outlier else 1.0,
    )
    return _clamp01(float(np.mean(component_scores)))


def _validate_inputs(
    window: np.ndarray, sample_rate: int, channel_names: tuple[str, ...]
) -> np.ndarray:
    if isinstance(sample_rate, bool) or not isinstance(sample_rate, (int, np.integer)):
        raise ValueError("sample_rate must be a positive integer")
    if sample_rate <= 0:
        raise ValueError("sample_rate must be a positive integer")

    values = np.asarray(window, dtype=float)
    if values.ndim != 2:
        raise ValueError("window must be a two-dimensional samples-by-channels array")
    if values.shape[0] < 2 or values.shape[1] == 0:
        raise ValueError("window must contain at least two samples and one channel")
    if len(channel_names) != values.shape[1]:
        raise ValueError("channel_names must match the number of window channels")
    if not all(isinstance(name, str) and name for name in channel_names):
        raise ValueError("channel_names must contain non-empty strings")
    if len(set(channel_names)) != len(channel_names):
        raise ValueError("channel_names must be unique")
    return values


def analyze_quality(
    window: np.ndarray,
    sample_rate: int,
    channel_names: tuple[str, ...] = DEFAULT_CHANNEL_NAMES,
) -> QualityResult:
    """Analyze a samples-by-channels EEG window without external side effects."""

    values = _validate_inputs(window, sample_rate, channel_names)
    outliers = _outlier_channels(values)

    scores = {
        name: _channel_score(values[:, index], sample_rate, index in outliers)
        for index, name in enumerate(channel_names)
    }

    flags: list[str] = []
    if not np.all(np.isfinite(values)):
        flags.append("non_finite_samples")
    if any(_is_flatline(values[:, index]) for index in range(values.shape[1])):
        flags.append("flatline")
    if any(
        _extreme_amplitude_ratio(values[:, index]) >= EXTREME_RATIO_THRESHOLD
        for index in range(values.shape[1])
    ):
        flags.append("extreme_amplitude")
    if any(_has_abrupt_step(values[:, index]) for index in range(values.shape[1])):
        flags.append("abrupt_steps")
    if any(
        _high_frequency_ratio(values[:, index], sample_rate)
        >= HIGH_FREQUENCY_RATIO_THRESHOLD
        for index in range(values.shape[1])
    ):
        flags.append("high_frequency_noise")
    if outliers:
        flags.append("channel_outlier")

    score = _clamp01(float(np.mean(tuple(scores.values()))))
    blocking = any(flag in BLOCKING_ARTIFACTS for flag in flags)
    if blocking or score < MARGINAL_SCORE_THRESHOLD:
        label = "bad"
    elif score < GOOD_SCORE_THRESHOLD:
        label = "marginal"
    else:
        label = "good"

    return QualityResult(
        score=score,
        label=label,
        channel_quality=scores,
        artifact_flags=tuple(flags),
    )
