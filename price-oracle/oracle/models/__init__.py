"""Statistical models (spec §6): value -> lifecycle -> forecast -> hazard ->
decision (stopping). Each has a transparent, dependency-light implementation
with a clean seam for the heavier PyMC upgrade (spec §6.3 Phase 2.5)."""

MODEL_VERSION = "0.1.0-fallback"  # bumped when the forecaster method changes
