from __future__ import annotations

from types import SimpleNamespace

import pytest

import ui


def band_sample(**overrides):
    sample = {
        "delta": 0.1,
        "theta": 0.2,
        "alpha": 0.3,
        "beta": 0.25,
        "gamma": 0.15,
    }
    sample.update(overrides)
    return sample


def channel_scores(score):
    return {name: score for name in ui.DEFAULT_CHANNEL_NAMES}


@pytest.mark.parametrize(
    ("label", "text", "color", "show_derived"),
    [
        ("good", "Good", "#2e7d32", True),
        ("marginal", "Marginal", "#ed6c02", True),
        ("bad", "Bad", "#d32f2f", False),
        ("unknown", "Unknown", "#616161", False),
        (None, "Unknown", "#616161", False),
    ],
)
def test_quality_presentation_and_state_visibility(label, text, color, show_derived):
    assert ui.quality_presentation(label) == (text, color)
    assert ui.should_show_derived_state(label) is show_derived


@pytest.mark.parametrize(
    ("label", "score", "expected"),
    [("good", 0.8, "good"), ("marginal", 0.6, "marginal"), ("bad", 0.2, "bad")],
)
def test_quality_label_for_sample_accepts_coherent_payload(label, score, expected):
    sample = band_sample(
        quality_label=label,
        signal_quality=score,
        channel_quality=channel_scores(score),
        artifact_flags=[],
    )

    assert ui.quality_label_for_sample(sample) == expected


@pytest.mark.parametrize(
    "overrides",
    [
        {"quality_label": "good"},
        {"quality_label": "good", "signal_quality": float("nan")},
        {
            "quality_label": "good",
            "signal_quality": "0.8",
            "channel_quality": channel_scores(0.8),
            "artifact_flags": [],
        },
        {
            "quality_label": "good",
            "signal_quality": 0.8,
            "channel_quality": channel_scores("0.8"),
            "artifact_flags": [],
        },
        {"quality_label": "good", "signal_quality": 0.8, "channel_quality": {"TP9": "bad"}},
        {
            "quality_label": "good",
            "signal_quality": 0.2,
            "channel_quality": {"TP9": 0.2},
            "artifact_flags": [],
        },
    ],
)
def test_quality_label_for_sample_fails_closed_on_incoherent_payload(overrides):
    assert ui.quality_label_for_sample(band_sample(**overrides)) == "unknown"


@pytest.mark.parametrize("score", [None, True, "0.8", "bad", float("nan"), float("inf"), -0.1, 1.1])
def test_quality_score_text_fails_closed(score):
    assert ui.quality_score_text(score) == "n/a"


def test_channel_quality_text_rejects_mixed_validity_payload():
    assert ui.channel_quality_text({"TP9": 0.8, "AF7": "bad"}) == "unavailable"


@pytest.mark.parametrize("flags", [None, "flatline", ["flatline", 42]])
def test_artifact_flags_text_marks_malformed_payload_unavailable(flags):
    assert ui.artifact_flags_text(flags) == "unavailable"


@pytest.mark.parametrize(
    "overrides",
    [
        {
            "quality_label": "good",
            "signal_quality": 0.8,
            "channel_quality": {"bogus": 0.8},
            "artifact_flags": [],
        },
        {
            "quality_label": "good",
            "signal_quality": 0.8,
            "channel_quality": channel_scores(0.7),
            "artifact_flags": [],
        },
        {
            "quality_label": "good",
            "signal_quality": 0.8,
            "channel_quality": channel_scores(0.8),
            "artifact_flags": ["future_flag"],
        },
    ],
)
def test_quality_label_for_sample_rejects_untrusted_metadata(overrides):
    assert ui.quality_label_for_sample(band_sample(**overrides)) == "unknown"


def test_has_valid_band_values_rejects_negative_values():
    assert ui.has_valid_band_values(band_sample(alpha=-0.1)) is False


@pytest.mark.parametrize("value", [True, "0.3"])
def test_has_valid_band_values_rejects_coerced_values(value):
    assert ui.has_valid_band_values(band_sample(alpha=value)) is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "Yes", "on", " ON "])
def test_simulation_allowed_accepts_explicit_true_values(value):
    assert ui.simulation_allowed({"SENSELAYER_ALLOW_SIMULATION": value}) is True


@pytest.mark.parametrize(
    "environment",
    [
        {},
        {"SENSELAYER_ALLOW_SIMULATION": "false"},
        {"SENSELAYER_ALLOW_SIMULATION": "0"},
        {"SENSELAYER_ALLOW_SIMULATION": "garbage"},
    ],
)
def test_simulation_allowed_fails_closed(environment):
    assert ui.simulation_allowed(environment) is False


def test_select_dashboard_sample_does_not_construct_simulator_when_disabled():
    calls = []

    def simulator_factory(_config):
        calls.append(True)
        raise AssertionError("simulator must not be constructed")

    sample, source = ui.select_dashboard_sample(
        None,
        {},
        allow_simulation=False,
        simulator_factory=simulator_factory,
    )

    assert sample is None
    assert source is None
    assert calls == []


def test_resolve_dashboard_sample_does_not_load_config_when_simulation_disabled():
    sample, source = ui.resolve_dashboard_sample(
        None,
        allow_simulation=False,
        config_loader=lambda: (_ for _ in ()).throw(AssertionError("config must not load")),
        simulator_factory=lambda _config: (_ for _ in ()).throw(AssertionError("unexpected simulation")),
    )

    assert sample is None
    assert source is None


def test_resolve_dashboard_sample_loads_config_only_for_enabled_simulation():
    summary = SimpleNamespace(delta=0.1, theta=0.2, alpha=0.3, beta=0.25, gamma=0.15)
    calls = []

    class FakeSimulator:
        def step(self):
            return object(), summary

    def config_loader():
        calls.append("config")
        return {"simulation": "config"}

    sample, source = ui.resolve_dashboard_sample(
        None,
        allow_simulation=True,
        config_loader=config_loader,
        simulator_factory=lambda _config: FakeSimulator(),
    )

    assert calls == ["config"]
    assert sample is not None
    assert source == "sim"


def test_select_dashboard_sample_uses_and_labels_simulation_when_enabled():
    summary = SimpleNamespace(delta=0.1, theta=0.2, alpha=0.3, beta=0.25, gamma=0.15)

    class FakeSimulator:
        def step(self):
            return object(), summary

    sample, source = ui.select_dashboard_sample(
        None,
        {},
        allow_simulation=True,
        simulator_factory=lambda _config: FakeSimulator(),
    )

    assert source == "sim"
    assert sample == {
        "delta": 0.1,
        "theta": 0.2,
        "alpha": 0.3,
        "beta": 0.25,
        "gamma": 0.15,
        "signal_quality": 1.0,
        "quality_label": "good",
        "channel_quality": {"TP9": 1.0, "AF7": 1.0, "AF8": 1.0, "TP10": 1.0},
        "artifact_flags": [],
    }


def test_select_dashboard_sample_prefers_live_data_without_simulator():
    live_sample = band_sample(quality_label="marginal")

    sample, source = ui.select_dashboard_sample(
        live_sample,
        {},
        allow_simulation=True,
        simulator_factory=lambda _config: (_ for _ in ()).throw(AssertionError("unexpected simulation")),
    )

    assert sample is live_sample
    assert source == "live"


def test_select_dashboard_sample_rejects_malformed_live_bands_without_simulation():
    sample, source = ui.select_dashboard_sample(
        {"delta": 0.1},
        {},
        allow_simulation=True,
        simulator_factory=lambda _config: (_ for _ in ()).throw(AssertionError("unexpected simulation")),
    )

    assert sample is None
    assert source is None


class FakeMetricTarget:
    def __init__(self, metrics):
        self.metrics = metrics

    def metric(self, label, value):
        self.metrics.append((label, value))


class FakeStreamlit:
    def __init__(self):
        self.metrics = []

    def columns(self, count):
        return tuple(FakeMetricTarget(self.metrics) for _ in range(count))

    def subheader(self, _text):
        pass

    def markdown(self, _text, **_kwargs):
        pass

    def caption(self, _text):
        pass

    def warning(self, _text):
        pass

    def progress(self, _value):
        pass


@pytest.mark.parametrize(
    "sample",
    [
        band_sample(),
        band_sample(
            quality_label="bad",
            signal_quality=0.2,
            channel_quality=channel_scores(0.2),
            artifact_flags=["flatline"],
        ),
        band_sample(
            quality_label="good",
            signal_quality=0.8,
            channel_quality={"TP9": "malformed"},
            artifact_flags=[],
        ),
    ],
)
def test_render_current_sample_suppresses_state_and_focus_for_untrusted_quality(monkeypatch, sample):
    fake_streamlit = FakeStreamlit()
    monkeypatch.setattr(ui, "st", fake_streamlit)

    ui.render_current_sample(sample, "live")

    assert ("State", ui.SUPPRESSED_TEXT) in fake_streamlit.metrics
    assert ("Focus %", ui.SUPPRESSED_TEXT) in fake_streamlit.metrics
