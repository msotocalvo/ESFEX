"""Demand estimation ML engine — unified hourly prediction.

Supports pluggable backends (XGBoost / TFT) with a single interface.
Both models predict hourly shape factors directly — no 3h aggregation
or Fourier reconstruction needed.

The model predicts dimensionless shape factors: demand_hour / annual_avg_MW.
Multiply by the annual average demand (MW) to get absolute MW values.
"""

from __future__ import annotations

import datetime
import logging
import math
from pathlib import Path
from typing import Optional

import numpy as np

from esfex.paths import MODELS_DIR as _CACHE_DIR

logger = logging.getLogger(__name__)

_XGB_FILENAME = "demand_model.xgb"

# TFT is intentionally disabled for ESFEX demand estimation: the use case is
# FORWARD per-node hourly generation over a multi-year horizon with no observed
# demand, for which the direct XGBoost shape predictor (all inputs known for any
# future hour) is the right tool. The TFT code path is kept intact and can be
# re-enabled by flipping this flag.
_TFT_ENABLED = False

# Feature columns — the 29-feature non-AR schema, matching the bundled
# MODELS_DIR/demand_model.xgb (84 countries). shape_lag_* are excluded: forward
# per-node generation has no demand history to seed autoregressive lags.
FEATURE_COLS = [
    "country_id",
    "log_gdp_per_cap", "log_pop_density", "urbanization",
    "temperature", "hdd", "cdd",
    "hour_of_day", "month", "day_of_week",
    "hour_sin", "hour_cos", "dow_sin", "dow_cos",
    "month_sin", "month_cos", "is_weekend",
    "is_holiday", "days_to_next_holiday", "days_from_prev_holiday",
    "latitude", "longitude",
    "temp_1d_lag", "temp_7d_mean", "temp_30d_mean", "temp_trend_7d",
    "temp_daily_max", "temp_daily_min", "temp_diurnal_range",
    # shape_lag_* disabled here: ECVI inference lacks demand history per pixel
]
# 29 features for ECVI. 32-feat parity variant lives at demand_model_ar.xgb.


# ── Feature construction (hourly) ───────────────────────────────────────────

def _cyclical(values: np.ndarray, period: float):
    angle = 2.0 * math.pi * values / period
    return np.sin(angle), np.cos(angle)


def build_hourly_features(
    gdp_per_capita: float,
    population: float,
    urbanization_pct: float,
    electricity_access_pct: float,
    temperature_hourly: np.ndarray,
    latitude: float,
    longitude: float,
    base_year: int,
    simulation_years: int = 1,
    hdd_base: float = 18.0,
    cdd_base: float = 24.0,
    gdp_growth_by_year: Optional[dict[int, float]] = None,
    pop_growth_by_year: Optional[dict[int, float]] = None,
) -> np.ndarray:
    """Build feature matrix at hourly resolution for inference.

    Parameters
    ----------
    temperature_hourly : ndarray (8760,)
        Base-year hourly temperature (°C). Reused for all sim years.

    Returns
    -------
    ndarray (simulation_years * 8760, 19)
    """
    hpy = 8760
    gdp_growth = gdp_growth_by_year or {}
    pop_growth = pop_growth_by_year or {}

    n_total = simulation_years * hpy
    X = np.zeros((n_total, len(FEATURE_COLS)), dtype=np.float64)

    # Base temperature features
    temp = temperature_hourly[:hpy] if len(temperature_hourly) >= hpy else np.full(hpy, 20.0)
    hdd = np.maximum(hdd_base - temp, 0.0)
    cdd = np.maximum(temp - cdd_base, 0.0)

    # Time features (same for each year, except GDP/pop growth)
    hours = np.arange(hpy)
    hour_of_day = hours % 24
    day_of_year = hours // 24

    h_sin, h_cos = _cyclical(hour_of_day.astype(float), 24.0)

    _month_days = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    month_arr = np.zeros(hpy, dtype=int)
    h = 0
    for m, md in enumerate(_month_days):
        month_arr[h:h + md * 24] = m
        h += md * 24
    m_sin, m_cos = _cyclical(month_arr.astype(float), 12.0)

    gdp = gdp_per_capita
    pop = population

    for y in range(simulation_years):
        yr = base_year + y

        if y > 0:
            gdp *= (1.0 + gdp_growth.get(yr, 0.0))
            pop *= (1.0 + pop_growth.get(yr, 0.0))

        jan1 = datetime.date(yr, 1, 1)
        dow = np.array([(jan1 + datetime.timedelta(days=int(d))).weekday()
                         for d in day_of_year])
        d_sin, d_cos = _cyclical(dow.astype(float), 7.0)
        is_wknd = (dow >= 5).astype(float)

        offset = y * hpy
        sl = slice(offset, offset + hpy)

        X[sl, 0] = math.log(max(gdp, 1.0))       # log_gdp
        X[sl, 1] = math.log(max(pop, 1.0))        # log_pop
        X[sl, 2] = urbanization_pct                # urbanization
        X[sl, 3] = electricity_access_pct          # elec_access
        X[sl, 4] = temp                            # temperature
        X[sl, 5] = hdd                             # hdd
        X[sl, 6] = cdd                             # cdd
        X[sl, 7] = hour_of_day                     # hour_of_day
        X[sl, 8] = month_arr                       # month
        X[sl, 9] = dow                             # day_of_week
        X[sl, 10] = h_sin                          # hour_sin
        X[sl, 11] = h_cos                          # hour_cos
        X[sl, 12] = d_sin                          # dow_sin
        X[sl, 13] = d_cos                          # dow_cos
        X[sl, 14] = m_sin                          # month_sin
        X[sl, 15] = m_cos                          # month_cos
        X[sl, 16] = is_wknd                        # is_weekend
        X[sl, 17] = latitude                       # latitude
        X[sl, 18] = longitude                      # longitude

    return X


# ── Unified model class ─────────────────────────────────────────────────────

class DemandMLModel:
    """Unified demand model with pluggable backends (XGBoost / TFT).

    Predicts hourly shape factors (demand / annual_avg_MW, mean ≈ 1.0).

    Engines:
      - 'auto': TFT if available, else XGBoost
      - 'tft': Temporal Fusion Transformer (deep learning)
      - 'xgboost': Gradient boosted trees
    """

    def __init__(self, engine: str = "xgboost"):
        self._model = None
        self._engine = engine

    @property
    def engine(self) -> str:
        return self._engine

    @classmethod
    def is_available(cls, engine: str = "auto") -> bool:
        if not _TFT_ENABLED and engine == "tft":
            engine = "xgboost"  # TFT disabled → resolve to XGBoost
        if _TFT_ENABLED and engine in ("auto", "tft"):
            try:
                from esfex.models.demand_tft import DemandTFTModel
                if DemandTFTModel.is_available():
                    return True
            except ImportError:
                pass
        if engine in ("auto", "xgboost"):
            if (_CACHE_DIR / _XGB_FILENAME).exists():
                return True
        return False

    @classmethod
    def load_bundled(cls, engine: str = "auto") -> "DemandMLModel":
        """Load the bundled model. With TFT disabled (default), resolves to
        XGBoost; TFT is loaded only when ``_TFT_ENABLED`` is True."""
        if not _TFT_ENABLED and engine == "tft":
            engine = "xgboost"  # TFT disabled → resolve any tft request to XGBoost
        if _TFT_ENABLED and engine in ("auto", "tft"):
            try:
                from esfex.models.demand_tft import DemandTFTModel
                if DemandTFTModel.is_available():
                    tft = DemandTFTModel.load_bundled()
                    instance = cls(engine="tft")
                    instance._model = tft
                    logger.info("Loaded TFT demand model")
                    return instance
            except (ImportError, Exception) as exc:
                if engine == "tft":
                    raise
                logger.debug("TFT not available: %s", exc)

        if engine in ("auto", "xgboost"):
            model_path = _CACHE_DIR / _XGB_FILENAME
            if not model_path.exists():
                raise FileNotFoundError(
                    f"No trained model at {model_path}. "
                    "Run 'esfex train-demand-model' to train one."
                )
            try:
                import xgboost as xgb
            except ImportError:
                raise ImportError(
                    "xgboost required. It ships with esfex; reinstall with: "
                    "pip install --upgrade --force-reinstall esfex"
                )
            instance = cls(engine="xgboost")
            instance._model = xgb.Booster()
            instance._model.load_model(str(model_path))
            logger.info("Loaded XGBoost demand model from %s", model_path)
            return instance

        raise FileNotFoundError(f"No model available for engine '{engine}'.")

    def predict(self, features: np.ndarray) -> np.ndarray:
        """Predict hourly demand shape factors.

        Parameters
        ----------
        features : ndarray (n_hours, 19)
            Feature matrix from build_hourly_features().

        Returns
        -------
        ndarray (n_hours,)
            Shape factors (mean ≈ 1.0).
        """
        if self._model is None:
            raise RuntimeError("Model not loaded.")

        if self._engine == "xgboost":
            import xgboost as xgb
            dmatrix = xgb.DMatrix(features, feature_names=FEATURE_COLS)
            predictions = self._model.predict(dmatrix)
        elif self._engine == "tft":
            predictions = self._model.predict_hourly_features(features)
        else:
            raise ValueError(f"Unknown engine: {self._engine}")

        predictions = np.maximum(predictions, 0.0)
        m = predictions.mean()
        if m > 0:
            predictions /= m
        return predictions

    def predict_raw(self, features: np.ndarray) -> np.ndarray:
        """Predict shape factors WITHOUT normalizing to mean=1.

        The raw mean encodes climate sensitivity: warmer inputs
        produce mean > 1.0, enabling climate-adjusted demand scaling.
        """
        if self._model is None:
            raise RuntimeError("Model not loaded.")

        if self._engine == "xgboost":
            import xgboost as xgb
            dmatrix = xgb.DMatrix(features, feature_names=FEATURE_COLS)
            predictions = self._model.predict(dmatrix)
        elif self._engine == "tft":
            predictions = self._model.predict_hourly_features(features)
        else:
            raise ValueError(f"Unknown engine: {self._engine}")

        return np.maximum(predictions, 0.0)

    def save(self, path: Path) -> None:
        if self._model is None:
            raise ValueError("No model to save.")
        if self._engine == "xgboost":
            path.parent.mkdir(parents=True, exist_ok=True)
            self._model.save_model(str(path))
        elif self._engine == "tft":
            self._model.save(path)

    def set_booster(self, booster) -> None:
        """Set XGBoost booster (used during training)."""
        self._model = booster
        self._engine = "xgboost"
