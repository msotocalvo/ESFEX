# -*- coding: utf-8 -*-
"""OTEC Studio — a non-linear workbench exposing the OTEX 0.3.1 library.

Additive to (not a replacement for) the linear ``OTECWizard``. The shared,
GUI-independent project model lives in :mod:`project`; the Qt shell in
:mod:`window`. See ``OTEX_STUDIO_DESIGN.md`` at the repo root.
"""

from esfex.visualization.workflows.otec_studio.project import (
    OtexProject,
    OtexScenario,
    ResourceData,
    StudioConfig,
    scenario_metrics,
)

__all__ = [
    "OtexProject",
    "OtexScenario",
    "ResourceData",
    "StudioConfig",
    "scenario_metrics",
]
