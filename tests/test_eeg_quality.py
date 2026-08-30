from __future__ import annotations

import pytest
import numpy as np

from tests.fixtures.eeg_windows import (
    FS,
    abrupt_steps,
    clean_alpha_10hz,
    extreme_amplitude,
    flatline,
    high_frequency_contamination,
    muse_scale_good_contact,
    muse_scale_recovered_contact,
    muse_scale_tp10_disconnected,
    non_finite_samples,
    one_channel_outlier,
)
from pipeline.eeg_quality import analyze_quality

EXPECTED_CHANNEL_KEYS = {"TP9", "AF7", "AF8", "TP10"}


@pytest.mark.parametrize(
    "window_factory, expected_label, expected_flag",
    [
        (clean_alpha_10hz, "good", None),
        (flatline, "bad", "flatline"),
        (extreme_amplitude, "bad", "extreme_amplitude"),
        (abrupt_steps, "bad", "abrupt_steps"),
        (high_frequency_contamination, "bad", "high_frequency_noise"),
        (one_channel_outlier, "bad", "channel_outlier"),
        (non_finite_samples, "bad", "non_finite_samples"),
    ],
)
def test_analyze_quality_matches_canonical_result_shape(window_factory, expected_label, expected_flag):
    result = analyze_quality(window_factory(), sample_rate=FS)

    assert result.label == expected_label
    assert 0.0 <= result.score <= 1.0
    assert set(result.channel_quality) == EXPECTED_CHANNEL_KEYS
    assert isinstance(result.artifact_flags, tuple)

    if expected_flag is None:
        assert result.artifact_flags == ()
    else:
        assert expected_flag in result.artifact_flags


@pytest.mark.parametrize(
    "window_factory",
    [
        clean_alpha_10hz,
        flatline,
        extreme_amplitude,
        abrupt_steps,
        high_frequency_contamination,
        one_channel_outlier,
        non_finite_samples,
    ],
)
def test_analyze_quality_score_is_normalized(window_factory):
    result = analyze_quality(window_factory(), sample_rate=FS)

    assert 0.0 <= result.score <= 1.0


@pytest.mark.parametrize(
    "window_factory",
    [
        clean_alpha_10hz,
        flatline,
        extreme_amplitude,
        abrupt_steps,
        high_frequency_contamination,
        one_channel_outlier,
        non_finite_samples,
    ],
)
def test_analyze_quality_returns_exact_per_channel_keys(window_factory):
    result = analyze_quality(window_factory(), sample_rate=FS)

    assert set(result.channel_quality) == EXPECTED_CHANNEL_KEYS


@pytest.mark.parametrize(
    "window",
    [
        np.zeros(16),
        np.zeros((2, 3, 4)),
        np.zeros((1, 4)),
        np.zeros((2, 0)),
    ],
)
def test_analyze_quality_rejects_invalid_window_dimensions(window):
    with pytest.raises(ValueError):
        analyze_quality(window, sample_rate=FS)


@pytest.mark.parametrize("sample_rate", [0, -1, 1.5, True])
def test_analyze_quality_rejects_invalid_sample_rate(sample_rate):
    with pytest.raises(ValueError):
        analyze_quality(clean_alpha_10hz(), sample_rate=sample_rate)


def test_analyze_quality_rejects_channel_name_count_mismatch():
    with pytest.raises(ValueError):
        analyze_quality(clean_alpha_10hz(), sample_rate=FS, channel_names=("TP9",))


def test_analyze_quality_flags_near_zero_variance_as_flatline():
    near_zero = clean_alpha_10hz() * 1e-7

    result = analyze_quality(near_zero, sample_rate=FS)

    assert result.label == "bad"
    assert "flatline" in result.artifact_flags


def test_analyze_quality_detects_equal_energy_channel_disagreement():
    window = clean_alpha_10hz()
    time = np.arange(window.shape[0], dtype=float) / FS
    window[:, 3] = np.sin(2 * np.pi * 3 * time)

    result = analyze_quality(window, sample_rate=FS)

    assert result.label == "bad"
    assert "channel_outlier" in result.artifact_flags
    assert result.channel_quality["TP10"] < result.channel_quality["TP9"]


def test_analyze_quality_keeps_large_finite_scores_normalized():
    result = analyze_quality(clean_alpha_10hz() * 1e154, sample_rate=FS)

    assert np.isfinite(result.score)
    assert all(np.isfinite(score) for score in result.channel_quality.values())
    assert 0.0 <= result.score <= 1.0


def test_analyze_quality_requires_peer_consensus_for_channel_outlier():
    time = np.arange(clean_alpha_10hz().shape[0], dtype=float) / FS
    phase_shifted = np.column_stack(
        [
            np.sin(2 * np.pi * 10 * time + phase)
            for phase in np.linspace(0, 1.5 * np.pi, 4)
        ]
    )

    result = analyze_quality(phase_shifted, sample_rate=FS)

    assert "channel_outlier" not in result.artifact_flags


def test_muse_scale_good_contact_is_not_rejected_by_dc_offset_or_normal_steps():
    result = analyze_quality(muse_scale_good_contact(), sample_rate=FS)

    assert result.label == "good"
    assert "extreme_amplitude" not in result.artifact_flags
    assert "abrupt_steps" not in result.artifact_flags


def test_muse_scale_disconnected_tp10_is_blocked():
    result = analyze_quality(muse_scale_tp10_disconnected(), sample_rate=FS)

    assert result.label == "bad"
    assert "extreme_amplitude" in result.artifact_flags
    assert result.channel_quality["TP10"] < result.channel_quality["TP9"]


def test_muse_scale_quality_worsens_and_recovers():
    before = analyze_quality(muse_scale_good_contact(), sample_rate=FS)
    disconnected = analyze_quality(muse_scale_tp10_disconnected(), sample_rate=FS)
    recovered = analyze_quality(muse_scale_recovered_contact(), sample_rate=FS)

    assert (before.label, disconnected.label, recovered.label) == (
        "good",
        "bad",
        "good",
    )
    assert recovered.artifact_flags == ()
