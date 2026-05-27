from __future__ import annotations

from dataclasses import dataclass
from typing import Dict


@dataclass
class ProcessedState:
    delta: float
    theta: float
    alpha: float
    beta: float
    gamma: float
    focus_score: float
    noise_score: float
    state_label: str


def clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def process_sample(sample: Dict) -> ProcessedState:
    delta = float(sample.get("delta", 0.0))
    theta = float(sample.get("theta", 0.0))
    alpha = float(sample.get("alpha", 0.0))
    beta = float(sample.get("beta", 0.0))
    gamma = float(sample.get("gamma", 0.0))

    total = delta + theta + alpha + beta + gamma
    if total <= 1e-9:
        nd = nt = na = nb = ng = 0.2
    else:
        nd, nt, na, nb, ng = [x / total for x in (delta, theta, alpha, beta, gamma)]

    focus_score = clamp01(0.50 + 0.48 * na + 0.30 * nb + 0.12 * ng - 0.22 * nd - 0.10 * nt)
    noise_score = clamp01(0.35 * nd + 0.22 * nt + 0.15 * ng)
    state_label = "Focus" if focus_score >= noise_score else "Noise"

    return ProcessedState(
        delta=delta,
        theta=theta,
        alpha=alpha,
        beta=beta,
        gamma=gamma,
        focus_score=focus_score,
        noise_score=noise_score,
        state_label=state_label,
    )
