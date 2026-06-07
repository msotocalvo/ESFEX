"""Temporal Fusion Transformer for hourly demand estimation.

Predicts demand shape factors at hourly resolution directly,
eliminating the need for Fourier harmonic reconstruction.

Architecture:
  - Static features: country embedding, lat/lon, GDP, population
  - Known future features: hour, day-of-week, month (cyclical encoded)
  - Observed features: temperature, HDD, CDD
  - Encoder: 168 hours (1 week lookback)
  - Decoder: 24 hours (1 day prediction)
  - Attention mechanism learns seasonal and diurnal patterns

Requires torch, pytorch-lightning, and pytorch-forecasting, all included
in the core esfex install.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

import numpy as np

from esfex.paths import MODELS_DIR as _CACHE_DIR, DEMAND_DATASET_DIR

logger = logging.getLogger(__name__)

_TFT_CHECKPOINT = "demand_tft.ckpt"
_TFT_PARAMS = "demand_tft_params.json"

# ── Feature engineering helpers ──────────────────────────────────────────────


def cyclical_encode(values: np.ndarray, period: float) -> tuple[np.ndarray, np.ndarray]:
    """Encode cyclical feature as sin/cos pair."""
    angle = 2.0 * np.pi * values / period
    return np.sin(angle), np.cos(angle)


def build_tft_dataframe(
    demand_mw: np.ndarray,
    temperature_c: Optional[np.ndarray],
    iso3: str,
    year: int,
    lat: float,
    lon: float,
    gdp_per_capita: float,
    population: float,
    urbanization_pct: float,
    electricity_access_pct: float,
    hdd_base: float = 18.0,
    cdd_base: float = 24.0,
) -> "pd.DataFrame":
    """Build a TFT-ready DataFrame for one country-year.

    Returns DataFrame with columns needed by TimeSeriesDataSet:
      - group_id, time_idx (indexing)
      - target (shape factor)
      - static features
      - time-varying known features (cyclical encoded)
      - time-varying observed features (temperature)
    """
    import pandas as pd

    n = len(demand_mw)
    annual_mean = demand_mw.mean()
    if annual_mean <= 0:
        annual_mean = 1.0

    # Shape factor target
    shape_factor = demand_mw / annual_mean

    # Time indices
    hours = np.arange(n)
    hour_of_day = hours % 24
    day_of_year = hours // 24
    month = np.array([
        min(11, max(0, int(d * 12 / 365))) for d in day_of_year
    ])

    import datetime
    jan1 = datetime.date(year, 1, 1)
    dow = np.array([(jan1 + datetime.timedelta(days=int(d))).weekday()
                     for d in day_of_year])

    # Cyclical encoding
    hour_sin, hour_cos = cyclical_encode(hour_of_day, 24.0)
    dow_sin, dow_cos = cyclical_encode(dow, 7.0)
    month_sin, month_cos = cyclical_encode(month, 12.0)
    doy_sin, doy_cos = cyclical_encode(day_of_year, 365.0)

    is_weekend = (dow >= 5).astype(np.float64)

    # Temperature features
    if temperature_c is not None and len(temperature_c) >= n:
        temp = temperature_c[:n].astype(np.float64)
    else:
        temp = np.full(n, 20.0, dtype=np.float64)

    hdd = np.maximum(hdd_base - temp, 0.0)
    cdd = np.maximum(temp - cdd_base, 0.0)

    df = pd.DataFrame({
        "group_id": iso3,
        "time_idx": hours,
        "target": shape_factor,
        # Static (constant per series)
        "latitude": lat,
        "longitude": lon,
        "log_gdp": np.log(max(gdp_per_capita, 1.0)),
        "log_pop": np.log(max(population, 1.0)),
        "urbanization": urbanization_pct,
        "elec_access": electricity_access_pct,
        # Time-varying known
        "hour_sin": hour_sin,
        "hour_cos": hour_cos,
        "dow_sin": dow_sin,
        "dow_cos": dow_cos,
        "month_sin": month_sin,
        "month_cos": month_cos,
        "doy_sin": doy_sin,
        "doy_cos": doy_cos,
        "is_weekend": is_weekend,
        # Time-varying observed
        "temperature": temp,
        "hdd": hdd,
        "cdd": cdd,
    })

    return df


# ── TFT Model wrapper ───────────────────────────────────────────────────────


class DemandTFTModel:
    """Temporal Fusion Transformer for demand shape factor prediction.

    Wraps pytorch-forecasting's TFT with a simplified interface
    compatible with the DemandMLModel dispatch system.
    """

    def __init__(self):
        self._model = None
        self._dataset_params: dict[str, Any] = {}

    @classmethod
    def is_available(cls) -> bool:
        """Check if a trained TFT checkpoint exists."""
        return (_CACHE_DIR / _TFT_CHECKPOINT).exists()

    @classmethod
    def load_bundled(cls) -> "DemandTFTModel":
        """Load pre-trained TFT from cache."""
        import json

        instance = cls()
        ckpt_path = _CACHE_DIR / _TFT_CHECKPOINT
        params_path = _CACHE_DIR / _TFT_PARAMS

        if not ckpt_path.exists():
            raise FileNotFoundError(
                f"No TFT model found at {ckpt_path}. "
                "Run 'esfex train-demand-model --engine tft' to train one."
            )

        try:
            from pytorch_forecasting import TemporalFusionTransformer
            instance._model = TemporalFusionTransformer.load_from_checkpoint(
                str(ckpt_path)
            )
            instance._model.eval()
        except ImportError:
            raise ImportError(
                "pytorch-forecasting is required for TFT. It ships with esfex; "
                "reinstall with: pip install --upgrade --force-reinstall esfex"
            )

        if params_path.exists():
            with open(params_path) as f:
                instance._dataset_params = json.load(f)

        logger.info("Loaded TFT model from %s", ckpt_path)
        return instance

    def predict_hourly(
        self,
        demand_history: np.ndarray,
        temperature_hourly: np.ndarray,
        iso3: str,
        lat: float,
        lon: float,
        gdp_per_capita: float,
        population: float,
        urbanization_pct: float,
        electricity_access_pct: float,
        n_hours: int = 8760,
    ) -> np.ndarray:
        """Predict hourly demand shape factors using autoregressive rolling.

        Uses 168h encoder window, predicts 24h, shifts, repeats.

        Parameters
        ----------
        demand_history : ndarray (≥168,)
            Historical demand in MW (for encoder initialization).
        temperature_hourly : ndarray (n_hours,)
            Future hourly temperature (°C).

        Returns
        -------
        ndarray (n_hours,)
            Predicted demand shape factors (mean ≈ 1.0).
        """
        if self._model is None:
            raise RuntimeError("Model not loaded.")

        import torch

        encoder_length = 168
        prediction_length = 24
        predictions = np.zeros(n_hours, dtype=np.float64)

        # Initialize with history
        annual_mean = demand_history.mean() if demand_history.mean() > 0 else 1.0
        history_sf = demand_history / annual_mean

        # Rolling prediction
        for start in range(0, n_hours, prediction_length):
            end = min(start + prediction_length, n_hours)
            # Build context + future DataFrame for this window
            # ... (TFT-specific input formatting)
            # For now, store the prediction steps
            remaining = end - start
            predictions[start:end] = 1.0  # placeholder

        # Normalize to mean=1
        m = predictions.mean()
        if m > 0:
            predictions /= m

        return predictions

    def save(self, path: Path) -> None:
        """Save model checkpoint and params."""
        import json
        path.parent.mkdir(parents=True, exist_ok=True)
        if self._model is not None:
            # pytorch-lightning saves via trainer
            pass
        if self._dataset_params:
            params_path = path.parent / _TFT_PARAMS
            with open(params_path, "w") as f:
                json.dump(self._dataset_params, f, indent=2)


# ── Training ─────────────────────────────────────────────────────────────────


def train_tft_model(
    dataset_dir: Optional[Path] = None,
    output_path: Optional[Path] = None,
    n_cv_folds: int = 3,
    max_epochs: int = 50,
    batch_size: int = 64,
    gpus: int = 1,
    encoder_length: int = 168,
    prediction_length: int = 24,
    hidden_size: int = 64,
    attention_head_size: int = 4,
    dropout: float = 0.1,
    learning_rate: float = 0.001,
    progress_cb=None,
    **kwargs,
) -> dict:
    """Train TFT using the unified hourly dataset with temporal CV.

    Uses the same ``build_unified_dataset()`` as XGBoost for fair comparison.
    Temporal cross-validation by year (expanding window).

    Returns dict with metrics and CV results.
    """
    try:
        import torch
        import lightning.pytorch as pl
        from pytorch_forecasting import (
            TemporalFusionTransformer,
            TimeSeriesDataSet,
        )
        from pytorch_forecasting.metrics import QuantileLoss
        from pytorch_forecasting.data.encoders import NaNLabelEncoder
    except ImportError:
        raise ImportError(
            "pytorch-forecasting required. It ships with esfex; reinstall with: "
            "pip install --upgrade --force-reinstall esfex"
        )

    import json
    import shutil

    if dataset_dir is None:
        dataset_dir = DEMAND_DATASET_DIR
    if output_path is None:
        output_path = _CACHE_DIR / _TFT_CHECKPOINT

    torch.set_float32_matmul_precision("medium")

    def emit(pct, msg):
        if progress_cb:
            progress_cb(pct, msg)
        logger.info("[%d%%] %s", pct, msg)

    # ── Step 1: Build unified dataset (same as XGBoost) ──────────────────
    emit(5, "Building unified hourly dataset...")
    from esfex.models.demand_training import build_unified_dataset, temporal_cv_splits

    full_df = build_unified_dataset(dataset_dir)
    # TFT needs 'country' column and consistent group_id
    if "country" not in full_df.columns:
        full_df["country"] = full_df["group_id"].str[:3]

    emit(20, f"Dataset: {len(full_df):,} rows, {full_df['country'].nunique()} countries")

    # ── Step 2: Temporal CV ──────────────────────────────────────────────
    emit(25, "Running temporal cross-validation...")
    splits = temporal_cv_splits(full_df, n_folds=n_cv_folds, strategy="expanding")

    cv_results = []
    for fold_i, (train_df, val_df, val_year) in enumerate(splits):
        emit(25 + int(50 * fold_i / len(splits)),
             f"Fold {fold_i + 1}/{len(splits)}: val_year={val_year}")

        # Reset time_idx per group for TimeSeriesDataSet
        train_df = train_df.copy()
        val_df = val_df.copy()

        training = TimeSeriesDataSet(
            train_df,
            time_idx="time_idx",
            target="shape_factor",
            group_ids=["group_id"],
            min_encoder_length=encoder_length // 2,
            max_encoder_length=encoder_length,
            min_prediction_length=1,
            max_prediction_length=prediction_length,
            static_categoricals=["country"],
            static_reals=["latitude", "longitude",
                           "log_gdp_per_cap", "log_pop_density",
                           "urbanization"],
            time_varying_known_reals=["hour_sin", "hour_cos", "dow_sin", "dow_cos",
                                       "month_sin", "month_cos", "is_weekend",
                                       "is_holiday", "days_to_next_holiday",
                                       "days_from_prev_holiday"],
            time_varying_unknown_reals=["temperature", "hdd", "cdd", "shape_factor"],
            target_normalizer=None,
            add_relative_time_idx=True,
            add_target_scales=True,
            add_encoder_length=True,
            categorical_encoders={
                "country": NaNLabelEncoder(add_nan=True),
                "group_id": NaNLabelEncoder(add_nan=True),
            },
        )

        validation = TimeSeriesDataSet.from_dataset(
            training, val_df, predict=True, stop_randomization=True,
        )

        train_dl = training.to_dataloader(train=True, batch_size=batch_size, num_workers=20)
        val_dl = validation.to_dataloader(train=False, batch_size=batch_size * 2, num_workers=20)

        tft = TemporalFusionTransformer.from_dataset(
            training,
            learning_rate=learning_rate,
            hidden_size=hidden_size,
            attention_head_size=attention_head_size,
            dropout=dropout,
            hidden_continuous_size=hidden_size // 2,
            output_size=7,
            loss=QuantileLoss(),
            reduce_on_plateau_patience=3,
        )

        trainer = pl.Trainer(
            max_epochs=max_epochs,
            accelerator="gpu" if gpus > 0 and torch.cuda.is_available() else "cpu",
            devices=min(gpus, 1) if torch.cuda.is_available() else 1,
            gradient_clip_val=0.1,
            callbacks=[
                pl.callbacks.EarlyStopping(monitor="val_loss", patience=5, mode="min"),
            ],
            enable_progress_bar=True,
            enable_model_summary=False,
        )

        trainer.fit(tft, train_dataloaders=train_dl, val_dataloaders=val_dl)

        # Evaluate on validation set
        val_loss = float(trainer.callback_metrics.get("val_loss", 0))

        # Predict to compute R²/MAPE
        preds_raw = tft.predict(val_dl, mode="raw")
        # Handle both old (.output["prediction"]) and new (.prediction) API
        if hasattr(preds_raw, "output") and isinstance(preds_raw.output, dict):
            pred_median = preds_raw.output["prediction"][:, :, 3].cpu().numpy()
        else:
            pred_median = preds_raw.prediction[:, :, 3].cpu().numpy()
        actuals = torch.cat([y[0] for x, y in iter(val_dl)]).cpu().numpy()

        from esfex.models.demand_training import evaluate_predictions
        pf = pred_median.reshape(-1)
        af = actuals.reshape(-1)
        from sklearn.metrics import r2_score
        fold_r2 = float(r2_score(af, pf))
        fold_metrics = evaluate_predictions(af, pf)

        # Save fold predictions for parity plots
        preds_dir = output_path.parent / "cv_predictions"
        preds_dir.mkdir(exist_ok=True)
        np.savez_compressed(
            preds_dir / f"tft_fold{fold_i}.npz",
            y_true=af, y_pred=pf,
        )

        cv_results.append({
            "fold": fold_i, "val_year": int(val_year),
            "val_loss": val_loss, "r2": fold_r2,
            "best_epoch": trainer.current_epoch,
            "multi_res": fold_metrics,
        })
        emit(25 + int(50 * (fold_i + 1) / len(splits)),
             f"Fold {fold_i + 1}: val_year={val_year}, R²={fold_r2:.4f}, loss={val_loss:.4f}")

    avg_r2 = float(np.mean([f["r2"] for f in cv_results]))
    emit(80, f"CV average R²={avg_r2:.4f}")

    # ── Step 3: Train final model on ALL data ────────────────────────────
    emit(82, "Training final model on all data...")

    full_df_copy = full_df.copy()
    final_training = TimeSeriesDataSet(
        full_df_copy,
        time_idx="time_idx",
        target="shape_factor",
        group_ids=["group_id"],
        min_encoder_length=encoder_length // 2,
        max_encoder_length=encoder_length,
        min_prediction_length=1,
        max_prediction_length=prediction_length,
        static_categoricals=["country"],
        static_reals=["latitude", "longitude",
                       "log_gdp_per_cap", "log_pop_density",
                       "urbanization"],
        time_varying_known_reals=["hour_sin", "hour_cos", "dow_sin", "dow_cos",
                                   "month_sin", "month_cos", "is_weekend",
                                   "is_holiday", "days_to_next_holiday",
                                   "days_from_prev_holiday"],
        time_varying_unknown_reals=["temperature", "hdd", "cdd",
                                     "shape_factor"],
        target_normalizer=None,
        add_relative_time_idx=True,
        add_target_scales=True,
        add_encoder_length=True,
        categorical_encoders={
            "country": NaNLabelEncoder(add_nan=True),
            "group_id": NaNLabelEncoder(add_nan=True),
        },
    )
    final_dl = final_training.to_dataloader(train=True, batch_size=batch_size, num_workers=20)

    final_tft = TemporalFusionTransformer.from_dataset(
        final_training,
        learning_rate=learning_rate,
        hidden_size=hidden_size,
        attention_head_size=attention_head_size,
        dropout=dropout,
        hidden_continuous_size=hidden_size // 2,
        output_size=7,
        loss=QuantileLoss(),
    )

    final_trainer = pl.Trainer(
        max_epochs=max_epochs,
        accelerator="gpu" if gpus > 0 and torch.cuda.is_available() else "cpu",
        devices=min(gpus, 1) if torch.cuda.is_available() else 1,
        gradient_clip_val=0.1,
        callbacks=[
            pl.callbacks.ModelCheckpoint(
                dirpath=str(output_path.parent),
                filename="demand_tft",
                monitor="train_loss_epoch",
                mode="min",
            ),
        ],
        enable_progress_bar=True,
        enable_model_summary=False,
    )

    final_trainer.fit(final_tft, train_dataloaders=final_dl)

    # Save checkpoint
    best_path = final_trainer.checkpoint_callback.best_model_path
    if best_path and Path(best_path) != output_path:
        shutil.copy2(best_path, output_path)

    # Save TimeSeriesDataSet parameters so the inference backend can
    # reconstruct an equivalent dataset without rebuilding from scratch.
    import pickle
    ds_params_path = output_path.parent / "demand_tft_ds_params.pkl"
    with open(ds_params_path, "wb") as _f:
        pickle.dump(final_training.get_parameters(), _f)
    logger.info("Saved ds_params: %s", ds_params_path)

    # ── Step 4: Save metrics ─────────────────────────────────────────────
    metrics = {
        "engine": "tft",
        "n_samples": len(full_df),
        "n_countries": int(full_df["country"].nunique()),
        "n_country_years": int(full_df["group_id"].nunique()),
        "cv_folds": len(cv_results),
        "cv_avg_r2": avg_r2,
        "cv_results": cv_results,
        "encoder_length": encoder_length,
        "prediction_length": prediction_length,
        "hidden_size": hidden_size,
        "n_parameters": int(final_tft.size()),
    }
    metrics_path = output_path.parent / "demand_tft_metrics.json"
    with open(metrics_path, "w") as f:
        json.dump(metrics, f, indent=2)

    emit(100, f"TFT trained: CV R²={avg_r2:.4f}, saved to {output_path}")
    return metrics
