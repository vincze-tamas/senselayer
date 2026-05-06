import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd

DEFAULT_CONFIG = {
    "simulation": {
        "sample_rate": 256,
        "window_seconds": 4,
        "artifact_probability": 0.18,
        "blink_probability": 0.08,
        "motion_probability": 0.10,
        "drift_strength": 0.85,
        "base_noise": 1.8,
    },
    "band_weights": {
        "raw_mix": {
            "delta": 0.80,
            "theta": 0.75,
            "alpha": 0.60,
            "beta": 0.42,
            "gamma": 0.22,
        },
        "focus_score": {
            "delta": -0.22,
            "theta": -0.10,
            "alpha": 0.48,
            "beta": 0.30,
            "gamma": 0.12,
            "bias": 0.50,
        },
        "noise_score": {
            "delta": 0.40,
            "theta": 0.28,
            "alpha": 0.00,
            "beta": 0.00,
            "gamma": 0.18,
            "raw_std": 0.14,
            "bias": 0.00,
        },
    },
    "thresholds": {
        "focus": 0.62,
        "noise": 0.55,
    },
    "colors": {
        "delta": "#ff4d4f",
        "theta": "#ff9f43",
        "alpha": "#2ecc71",
        "beta": "#3498db",
        "gamma": "#9b59b6",
    },
}

BANDS = ("delta", "theta", "alpha", "beta", "gamma")
LEGACY_SIM_KEYS = {
    "sample_rate": ("simulation", "sample_rate"),
    "window_seconds": ("simulation", "window_seconds"),
    "artifact_probability": ("simulation", "artifact_probability"),
    "blink_probability": ("simulation", "blink_probability"),
    "motion_probability": ("simulation", "motion_probability"),
    "focus_threshold": ("thresholds", "focus"),
    "noise_threshold": ("thresholds", "noise"),
}


@dataclass
class WindowSummary:
    timestamp: float
    delta: float
    theta: float
    alpha: float
    beta: float
    gamma: float
    focus_score: float
    noise_score: float
    focus: bool
    noisy: bool
    state_label: str


class Muse2Simulator:
    def __init__(self, config: Optional[Dict] = None, seed: Optional[int] = None):
        self.config = self._merge_config(config or {})
        self.rng = np.random.default_rng(seed)
        sim = self.config["simulation"]
        self.sample_rate = int(sim["sample_rate"])
        self.window_seconds = int(sim["window_seconds"])
        self.n_samples = self.sample_rate * self.window_seconds

    def _deep_update(self, target: Dict, updates: Dict) -> Dict:
        for key, value in updates.items():
            if isinstance(value, dict) and isinstance(target.get(key), dict):
                self._deep_update(target[key], value)
            else:
                target[key] = value
        return target

    def _merge_config(self, config: Dict) -> Dict:
        merged = json.loads(json.dumps(DEFAULT_CONFIG))
        legacy_present = any(key in config for key in LEGACY_SIM_KEYS) or isinstance(config.get("bands"), dict)
        if legacy_present:
            for legacy_key, path in LEGACY_SIM_KEYS.items():
                if legacy_key in config:
                    section, key = path
                    merged[section][key] = config[legacy_key]
            if isinstance(config.get("simulation"), dict):
                merged["simulation"] = self._deep_update(merged["simulation"], config["simulation"])
            if isinstance(config.get("thresholds"), dict):
                merged["thresholds"] = self._deep_update(merged["thresholds"], config["thresholds"])
            if isinstance(config.get("band_weights"), dict):
                merged["band_weights"] = self._deep_update(merged["band_weights"], config["band_weights"])
            if isinstance(config.get("colors"), dict):
                merged["colors"] = self._deep_update(merged["colors"], config["colors"])
            if isinstance(config.get("bands"), dict):
                for band_name, band_cfg in config["bands"].items():
                    if band_name in BANDS and isinstance(band_cfg, dict) and "mix" in band_cfg:
                        merged["band_weights"]["raw_mix"][band_name] = band_cfg["mix"]
            return merged

        for key, value in config.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = self._deep_update(merged[key], value)
            else:
                merged[key] = value
        return merged

    def _band_signal(self, band_name: str, t: np.ndarray, phase: float) -> np.ndarray:
        raw_mix = self.config["band_weights"]["raw_mix"]
        amplitude = {
            "delta": 22.0,
            "theta": 16.0,
            "alpha": 18.0,
            "beta": 11.0,
            "gamma": 7.0,
        }[band_name]
        frequency = {
            "delta": 2.0,
            "theta": 6.0,
            "alpha": 10.0,
            "beta": 20.0,
            "gamma": 38.0,
        }[band_name]
        band_noise = {
            "delta": 2.5,
            "theta": 2.0,
            "alpha": 1.8,
            "beta": 1.4,
            "gamma": 1.0,
        }[band_name]
        weight = float(raw_mix.get(band_name, 1.0))
        base = amplitude * weight * np.sin(2 * np.pi * frequency * t + phase)
        harmonic = 0.18 * amplitude * weight * np.sin(2 * np.pi * frequency * 0.5 * t + phase / 2)
        noise = self.rng.normal(0, band_noise, size=t.shape)
        return base + harmonic + noise

    def _artifact(self, t: np.ndarray) -> np.ndarray:
        sim = self.config["simulation"]
        artifact = np.zeros_like(t)
        if self.rng.random() < sim["blink_probability"]:
            center = self.rng.integers(int(0.2 * len(t)), int(0.8 * len(t)))
            width = self.rng.integers(max(2, len(t) // 40), max(4, len(t) // 15))
            spike = np.exp(-((np.arange(len(t)) - center) ** 2) / (2 * width**2))
            artifact += spike * self.rng.uniform(45, 90)
        if self.rng.random() < sim["motion_probability"]:
            wander = self.rng.normal(0, 1.0, size=len(t)).cumsum()
            wander = (wander - wander.mean()) / (wander.std() + 1e-6)
            artifact += wander * self.rng.uniform(8, 16)
        if self.rng.random() < sim["artifact_probability"]:
            burst_idx = self.rng.integers(0, len(t))
            burst_len = self.rng.integers(max(8, len(t) // 25), max(20, len(t) // 8))
            end = min(len(t), burst_idx + burst_len)
            artifact[burst_idx:end] += self.rng.uniform(20, 50) * np.sin(
                np.linspace(0, np.pi * self.rng.uniform(2, 6), end - burst_idx)
            )
        drift = sim.get("drift_strength", 0.85) * np.sin(2 * np.pi * 0.12 * t)
        return artifact + drift

    def generate_window(self) -> pd.DataFrame:
        t = np.arange(self.n_samples) / self.sample_rate
        phases = self.rng.uniform(0, 2 * np.pi, size=len(BANDS))
        signals = {band: self._band_signal(band, t, phase) for band, phase in zip(BANDS, phases)}

        mix = self.config["band_weights"]["raw_mix"]
        raw = sum(float(mix.get(band, 0.0)) * signals[band] for band in BANDS)
        raw += self._artifact(t)
        raw += self.rng.normal(0, self.config["simulation"]["base_noise"], size=len(t))

        df = pd.DataFrame({"time": t, "raw": raw})
        for band in BANDS:
            df[band] = signals[band]
        return df

    @staticmethod
    def _band_power(signal: np.ndarray) -> float:
        centered = signal - np.mean(signal)
        return float(np.sum(np.square(centered)) / max(len(centered), 1))

    def summarize(self, frame: pd.DataFrame) -> WindowSummary:
        powers = {band: self._band_power(frame[band].to_numpy()) for band in BANDS}
        total = sum(powers.values()) + 1e-6
        norm = {band: powers[band] / total for band in BANDS}
        focus_weights = self.config["band_weights"]["focus_score"]
        noise_weights = self.config["band_weights"]["noise_score"]
        focus_score = float(np.clip(
            focus_weights["bias"]
            + focus_weights["alpha"] * norm["alpha"]
            + focus_weights["beta"] * norm["beta"]
            + focus_weights["gamma"] * norm["gamma"]
            + focus_weights["delta"] * norm["delta"]
            + focus_weights["theta"] * norm["theta"],
            0,
            1,
        ))
        noise_score = float(np.clip(
            noise_weights["bias"]
            + noise_weights["delta"] * norm["delta"]
            + noise_weights["theta"] * norm["theta"]
            + noise_weights["gamma"] * norm["gamma"]
            + noise_weights["raw_std"] * (frame["raw"].std() / 60.0),
            0,
            1,
        ))
        focus = focus_score >= float(self.config["thresholds"]["focus"])
        noisy = noise_score >= float(self.config["thresholds"]["noise"])
        state_label = "Focus" if focus and not noisy else "Noise" if noisy and not focus else ("Focus" if focus_score >= noise_score else "Noise")
        return WindowSummary(
            timestamp=float(frame["time"].iloc[-1]),
            delta=powers["delta"],
            theta=powers["theta"],
            alpha=powers["alpha"],
            beta=powers["beta"],
            gamma=powers["gamma"],
            focus_score=focus_score,
            noise_score=noise_score,
            focus=focus,
            noisy=noisy,
            state_label=state_label,
        )

    def step(self) -> Tuple[pd.DataFrame, WindowSummary]:
        frame = self.generate_window()
        return frame, self.summarize(frame)


def load_config(path: str = "config.json") -> Dict:
    p = Path(path)
    if not p.exists():
        return json.loads(json.dumps(DEFAULT_CONFIG))
    with p.open("r", encoding="utf-8") as f:
        loaded = json.load(f)
    return Muse2Simulator(loaded).config


def demo() -> None:
    sim = Muse2Simulator(load_config())
    frame, summary = sim.step()
    print(frame.head().to_string(index=False))
    print(json.dumps(asdict(summary), indent=2))


if __name__ == "__main__":
    demo()
