"""
EV fleet electrification adoption models.

This module re-exports from the evrex standalone library.
"""

from evrex import (
    DEFAULT_CATEGORIES,
    DEFAULT_CATEGORY_SHARES,
    DEFAULT_ENERGY_CONSUMPTION,
    EVAdoptionCurve,
    EVMacroData,
    EVValidationData,
    TransportContext,
    fit_adoption_to_ev_config,
    run_ev_bass_diffusion,
    run_ev_logistic_adoption,
    run_ev_policy_driven,
    run_ev_tco_parity,
)

__all__ = [
    "DEFAULT_CATEGORIES",
    "DEFAULT_CATEGORY_SHARES",
    "DEFAULT_ENERGY_CONSUMPTION",
    "EVAdoptionCurve",
    "EVMacroData",
    "EVValidationData",
    "TransportContext",
    "fit_adoption_to_ev_config",
    "run_ev_bass_diffusion",
    "run_ev_logistic_adoption",
    "run_ev_policy_driven",
    "run_ev_tco_parity",
]
