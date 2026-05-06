# BCI Simulator

## Goal
Build a lightweight Python BCI simulator that generates synthetic Muse 2-style EEG data and classifies each sample as Focus or Noise.

## Core signals
- Generate synthetic bands: Delta, Theta, Alpha, Beta, Gamma
- Add noise, drift, and occasional artifacts
- Classify state with simple threshold-based logic or other lightweight heuristics

## File structure
- sim.py: EEG signal generation, band synthesis, classification logic, data output
- ui.py: Streamlit dashboard for live visualization
- config.json: simulation parameters, band weights, thresholds, colors
- requirements.txt: Python dependencies
- README.md: project overview and setup notes

## UI requirements (wow component)
Use Streamlit for the dashboard.

The dashboard should include:
- A human head / brain outline visualization
- Brainwaves drawn around the brain outline
- Color mapping for the waves:
  - Delta: Red
  - Theta: Orange
  - Alpha: Green
  - Beta: Blue
  - Gamma: Purple
- Wave intensity and line width should react to simulator data
- A clear current-state indicator for Focus / Noise

## First coder task
Implement sim.py so it can emit structured samples with band amplitudes and a state label, then implement ui.py as a Streamlit page that renders the head/brain outline, colored wave layers, and the current state.

## Notes
Keep the UI visually strong and easy to read. The brain outline should feel like a demo-ready wow view rather than a plain chart.
