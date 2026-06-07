"""TFT inference wrapper for pixel-level sensitivity analysis.

Wraps a trained TFT checkpoint so it can be queried per-pixel with
synthetic encoder+decoder histories, returning shape_factor predictions
and demand sensitivity (η) in %/°C. Used by
``ecvi_gridded.compute_demand_sensitivity`` to replace the legacy
XGBoost backend so the paper's sensitivity maps come from the same
model used for validation.

Why synthetic history? TFT is a sequence model that requires an encoder
window before it can decode. For sensitivity we don't have real hourly
demand at every pixel, so we fill the encoder with a representative
climatological temperature series + constant shape_factor=1. The
counterfactual (+ΔT) is applied only to the decoder window and we
compare `shape_decoded(T)` vs `shape_decoded(T+ΔT)`.

The encoder shape_factor being constant at 1 means the encoder carries
no temporal shape information — predictions are driven entirely by the
known time-varying features (calendar) and static features (log_gdp,
log_pop, urbanization, lat, lon). This is the methodologically honest
approach when no per-pixel demand history exists.
"""
from __future__ import annotations

import datetime
import logging
import math
import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import torch

from esfex.paths import MODELS_DIR

logger = logging.getLogger(__name__)

_DEFAULT_CKPT = MODELS_DIR / "demand_tft.ckpt"
_DEFAULT_DS_PARAMS = MODELS_DIR / "demand_tft_ds_params.pkl"


def _cyclical(values: np.ndarray, period: float):
    angle = 2.0 * math.pi * values / period
    return np.sin(angle), np.cos(angle)


@dataclass
class _SynthConfig:
    """How the synthetic encoder history is populated per pixel."""
    # Encoder temp: constant at pixel mean annual tas (or user-provided)
    encoder_length: int = 72
    prediction_length: int = 24
    # Reference hour for decoder: pick representative (season, hour, dow)
    # Default = noon on a summer weekday (peak-cooling proxy).
    decoder_month: int = 6        # June
    decoder_hour: int = 14        # 2 PM
    decoder_dow: int = 2          # Wednesday
    # Reference start year (for holiday lookup and static context).
    ref_year: int = 2025


class TFTSensitivityBackend:
    """Loads a trained TFT and exposes ``sensitivity_at_pixels``.

    Parameters
    ----------
    ckpt_path : Path, optional
        Lightning checkpoint. Default: ``MODELS_DIR/demand_tft.ckpt``.
    ds_params_path : Path, optional
        Pickle with ``TimeSeriesDataSet.get_parameters()`` from training.
        Default: ``MODELS_DIR/demand_tft_ds_params.pkl``.
    device : str, optional
        ``"cuda"`` (default if available) or ``"cpu"``.
    """

    def __init__(
        self,
        ckpt_path: Optional[Path] = None,
        ds_params_path: Optional[Path] = None,
        device: Optional[str] = None,
    ):
        try:
            from pytorch_forecasting import (
                TemporalFusionTransformer,
                TimeSeriesDataSet,
            )
        except ImportError as exc:
            raise ImportError(
                "pytorch-forecasting required. It ships with esfex; reinstall "
                "with: pip install --upgrade --force-reinstall esfex"
            ) from exc

        self._TFTClass = TemporalFusionTransformer
        self._TSDClass = TimeSeriesDataSet

        self.ckpt_path = Path(ckpt_path or _DEFAULT_CKPT)
        self.ds_params_path = Path(ds_params_path or _DEFAULT_DS_PARAMS)
        # Refuse a ds_params_path that resolves outside MODELS_DIR:
        # `pickle.load` on it is unrestricted code execution, so we
        # accept it only from the bundled models directory (where it
        # was written by our own training/extractor code). A caller
        # that genuinely needs to point at a different file can copy
        # it into MODELS_DIR first — making the trust decision explicit.
        try:
            self.ds_params_path.resolve().relative_to(MODELS_DIR.resolve())
        except ValueError:
            raise ValueError(
                f"ds_params_path must be inside the bundled MODELS_DIR "
                f"({MODELS_DIR}); refusing to pickle.load from {self.ds_params_path} "
                "(arbitrary pickle = code execution)."
            )
        if not self.ckpt_path.exists():
            raise FileNotFoundError(f"TFT checkpoint not found: {self.ckpt_path}")
        if not self.ds_params_path.exists():
            raise FileNotFoundError(
                f"ds_params pickle not found: {self.ds_params_path} "
                "(should be written by train_tft_model or post-hoc extractor)"
            )

        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.model = TemporalFusionTransformer.load_from_checkpoint(
            str(self.ckpt_path), map_location=self.device)
        self.model.eval()
        self.model.to(self.device)

        with open(self.ds_params_path, "rb") as f:
            self.ds_params = pickle.load(f)

        self.encoder_length = int(self.ds_params.get("max_encoder_length", 168))
        self.prediction_length = int(
            self.ds_params.get("max_prediction_length", 24))

        # Map canonical input names → actual column names used during training.
        # Handles both old (log_gdp/log_pop/elec_access) and new
        # (log_gdp_per_cap/log_pop_density) naming conventions.
        static_reals = self.ds_params.get("static_reals", [])
        self._gdp_col  = "log_gdp"  if "log_gdp"  in static_reals else "log_gdp_per_cap"
        self._pop_col  = "log_pop"  if "log_pop"   in static_reals else "log_pop_density"
        self._need_elec_access = "elec_access" in static_reals

        logger.info(
            "TFTSensitivityBackend loaded: ckpt=%s encoder=%d decoder=%d device=%s",
            self.ckpt_path.name, self.encoder_length, self.prediction_length,
            self.device,
        )

    # ── DataFrame construction ─────────────────────────────────────────

    def _build_pixel_batch_df(
        self,
        pixels: pd.DataFrame,
        temp_annual: np.ndarray,
        delta_t: float = 0.0,
        cfg: Optional[_SynthConfig] = None,
    ) -> pd.DataFrame:
        """Build encoder+decoder DataFrame for a batch of pixels.

        Parameters
        ----------
        pixels : DataFrame
            Required columns: ``pixel_id`` (int), ``iso3``, ``lat``, ``lon``,
            ``log_gdp_per_cap``, ``log_pop_density``, ``urbanization``.
        temp_annual : ndarray (len(pixels),)
            Annual mean temperature per pixel (°C).
        delta_t : float
            Added to temperature in the ENCODER window. Since temperature is a
            time_varying_unknown_real (only observed by the model in the encoder),
            applying delta_t to the encoder is the only way to affect predictions.
            This models "how does the decoder forecast change if the recent past
            was ΔT warmer?" — the correct causal direction for climate sensitivity.
        cfg : _SynthConfig, optional
        """
        cfg = cfg or _SynthConfig(
            encoder_length=self.encoder_length,
            prediction_length=self.prediction_length,
        )
        n_hours = cfg.encoder_length + cfg.prediction_length
        n_px = len(pixels)

        # Time indices (0..n_hours-1) common across pixels
        time_idx = np.arange(n_hours, dtype=np.int32)
        is_encoder = time_idx < cfg.encoder_length

        # Calendar placeholders: anchor decoder at cfg.decoder_hour/day,
        # walk backwards through the encoder with natural hour/dow progression.
        hours_of_day = np.zeros(n_hours, dtype=np.int32)
        dows = np.zeros(n_hours, dtype=np.int32)
        months = np.zeros(n_hours, dtype=np.int32)
        # Anchor: decoder_hour at encoder_length (first decoder step)
        anchor_datetime = datetime.datetime(
            cfg.ref_year, cfg.decoder_month, 15,
            cfg.decoder_hour, 0, 0,
        )
        for t in range(n_hours):
            dt = anchor_datetime + datetime.timedelta(
                hours=t - cfg.encoder_length)
            hours_of_day[t] = dt.hour
            dows[t] = dt.weekday()
            months[t] = dt.month - 1   # 0-indexed
        h_sin, h_cos = _cyclical(hours_of_day.astype(float), 24.0)
        d_sin, d_cos = _cyclical(dows.astype(float), 7.0)
        m_sin, m_cos = _cyclical(months.astype(float), 12.0)
        is_weekend = (dows >= 5).astype(np.float32)
        # Holiday features: zero (pixel has no holiday calendar by default)
        is_holiday = np.zeros(n_hours, dtype=np.float32)
        days_to_next_hol = np.full(n_hours, 7.0, dtype=np.float32)
        days_from_prev_hol = np.full(n_hours, 7.0, dtype=np.float32)

        # Build per-pixel arrays
        # Temperature: apply delta_t to encoder only (model cannot observe decoder temp)
        temp_base = np.repeat(temp_annual[:, None], n_hours, axis=1)  # (n_px, n_hours)
        temp_base[:, is_encoder] += delta_t
        hdd = np.maximum(18.0 - temp_base, 0.0)
        cdd = np.maximum(temp_base - 24.0, 0.0)

        # shape_factor: encoder = 1 (representative mean), decoder = NaN
        # (target; will be predicted). TimeSeriesDataSet requires the target
        # column for every row; we set decoder values to 1.0 as placeholder
        # — the model's prediction overrides them.
        shape_factor = np.ones((n_px, n_hours), dtype=np.float32)

        # Repeat per-pixel tile
        rows = n_px * n_hours
        # Tile pixel indices
        pixel_idx = np.repeat(np.arange(n_px), n_hours)
        time_tile = np.tile(time_idx, n_px)

        # Static per-pixel (repeated)
        iso3 = pixels["iso3"].values
        lat_arr = pixels["lat"].values
        lon_arr = pixels["lon"].values
        # Accept both naming conventions from callers; ds_params-matched names set at init
        log_gdp = (pixels["log_gdp_per_cap"].values
                   if "log_gdp_per_cap" in pixels.columns
                   else pixels["log_gdp"].values)
        log_pop = (pixels["log_pop_density"].values
                   if "log_pop_density" in pixels.columns
                   else pixels["log_pop"].values)
        urban = pixels["urbanization"].values

        df = pd.DataFrame({
            "group_id": [f"px_{i}" for i in pixel_idx],
            "country": iso3[pixel_idx],
            "zone": ["" for _ in range(rows)],
            "year": cfg.ref_year,
            "time_idx": time_tile,
            "shape_factor": shape_factor.reshape(-1),
            "demand_mw": 1.0,
            "annual_mean_mw": 1.0,
            self._gdp_col: log_gdp[pixel_idx],
            self._pop_col: log_pop[pixel_idx],
            "urbanization": urban[pixel_idx],
            "temperature": temp_base.reshape(-1),
            "hdd": hdd.reshape(-1),
            "cdd": cdd.reshape(-1),
            "hour_of_day": np.tile(hours_of_day, n_px),
            "month": np.tile(months, n_px),
            "day_of_week": np.tile(dows, n_px),
            "hour_sin": np.tile(h_sin, n_px).astype(np.float32),
            "hour_cos": np.tile(h_cos, n_px).astype(np.float32),
            "dow_sin": np.tile(d_sin, n_px).astype(np.float32),
            "dow_cos": np.tile(d_cos, n_px).astype(np.float32),
            "month_sin": np.tile(m_sin, n_px).astype(np.float32),
            "month_cos": np.tile(m_cos, n_px).astype(np.float32),
            "is_weekend": np.tile(is_weekend, n_px),
            "is_holiday": np.tile(is_holiday, n_px),
            "days_to_next_holiday": np.tile(days_to_next_hol, n_px),
            "days_from_prev_holiday": np.tile(days_from_prev_hol, n_px),
            "latitude": lat_arr[pixel_idx],
            "longitude": lon_arr[pixel_idx],
            "sample_weight": 1.0,
        })
        if self._need_elec_access:
            df["elec_access"] = 0.85  # representative global median
        return df

    # ── Prediction ─────────────────────────────────────────────────────

    def _predict_decoder_shape(self, df: pd.DataFrame) -> np.ndarray:
        """Run TFT on a prepared pixel-batch df; return decoder shape (n_px, pred_len)."""
        ts_ds = self._TSDClass.from_parameters(
            self.ds_params, df, predict=True, stop_randomization=True)
        dl = ts_ds.to_dataloader(
            train=False, batch_size=max(64, len(df) // 192), num_workers=0)
        preds = []
        with torch.no_grad():
            for batch in dl:
                x, _ = batch
                x = {k: (v.to(self.device) if torch.is_tensor(v) else v)
                     for k, v in x.items()}
                out = self.model(x)
                # pytorch_forecasting returns namedtuple/dict; use median quantile (index 3)
                pred = (out["prediction"] if isinstance(out, dict)
                        else out.prediction)
                if pred.ndim == 3:
                    # (batch, time, quantile) → median
                    median = pred[..., pred.shape[-1] // 2]
                else:
                    median = pred
                preds.append(median.cpu().numpy())
        return np.concatenate(preds, axis=0)

    # ── Annual (per-country, 8760h) projection ─────────────────────────

    def predict_annual_shape(
        self,
        iso3: str,
        lat: float,
        lon: float,
        log_gdp_per_cap: float,
        log_pop_density: float,
        urbanization: float,
        temperature_hourly: np.ndarray,
        year: int,
    ) -> np.ndarray:
        """Predict hourly shape factors for a full year using sliding TFT
        windows. Produces 8760 values.

        Strategy: build a single 8760+encoder_length DataFrame for the
        country, then let pytorch-forecasting's TimeSeriesDataSet carve
        all valid decoder windows. One forward pass batched internally.
        """
        enc = self.encoder_length
        pred = self.prediction_length

        if len(temperature_hourly) < 8760:
            raise ValueError("temperature_hourly must have >=8760 values")
        t_year = temperature_hourly[:8760].astype(np.float64)
        # Encoder prefix: reuse end-of-year temps to simulate "previous" hours
        t_prefix = t_year[-enc:]
        t_full = np.concatenate([t_prefix, t_year])  # length 8760 + enc

        n_hours = len(t_full)
        time_idx = np.arange(n_hours, dtype=np.int32)

        # Calendar features (Jan 1 starts at index enc)
        anchor = datetime.datetime(year, 1, 1, 0, 0, 0)
        hours_of_day = np.zeros(n_hours, dtype=np.int32)
        dows = np.zeros(n_hours, dtype=np.int32)
        months = np.zeros(n_hours, dtype=np.int32)
        for t in range(n_hours):
            dt = anchor + datetime.timedelta(hours=t - enc)
            hours_of_day[t] = dt.hour
            dows[t] = dt.weekday()
            months[t] = dt.month - 1
        h_sin, h_cos = _cyclical(hours_of_day.astype(float), 24.0)
        d_sin, d_cos = _cyclical(dows.astype(float), 7.0)
        m_sin, m_cos = _cyclical(months.astype(float), 12.0)
        is_weekend = (dows >= 5).astype(np.float32)

        hdd = np.maximum(18.0 - t_full, 0.0)
        cdd = np.maximum(t_full - 24.0, 0.0)
        shape_factor = np.ones(n_hours, dtype=np.float32)

        df = pd.DataFrame({
            "group_id": f"proj_{iso3}_{year}",
            "country": iso3,
            "zone": "",
            "year": year,
            "time_idx": time_idx,
            "shape_factor": shape_factor,
            "demand_mw": 1.0,
            "annual_mean_mw": 1.0,
            self._gdp_col: log_gdp_per_cap,
            self._pop_col: log_pop_density,
            "urbanization": urbanization,
            "temperature": t_full,
            "hdd": hdd,
            "cdd": cdd,
            "hour_of_day": hours_of_day,
            "month": months,
            "day_of_week": dows,
            "hour_sin": h_sin.astype(np.float32),
            "hour_cos": h_cos.astype(np.float32),
            "dow_sin": d_sin.astype(np.float32),
            "dow_cos": d_cos.astype(np.float32),
            "month_sin": m_sin.astype(np.float32),
            "month_cos": m_cos.astype(np.float32),
            "is_weekend": is_weekend,
            "is_holiday": 0.0,
            "days_to_next_holiday": 7.0,
            "days_from_prev_holiday": 7.0,
            "latitude": lat,
            "longitude": lon,
            "sample_weight": 1.0,
            **({"elec_access": 0.85} if self._need_elec_access else {}),
        })

        ts_ds = self._TSDClass.from_parameters(
            self.ds_params, df, predict=False, stop_randomization=True)
        dl = ts_ds.to_dataloader(
            train=False, batch_size=256, num_workers=0)

        # Aggregate predictions across overlapping decoder windows.
        shape_accum = np.zeros(n_hours, dtype=np.float64)
        shape_count = np.zeros(n_hours, dtype=np.int32)
        with torch.no_grad():
            for batch in dl:
                x, _ = batch
                x = {k: (v.to(self.device) if torch.is_tensor(v) else v)
                     for k, v in x.items()}
                out = self.model(x)
                pred_t = (out["prediction"] if isinstance(out, dict)
                          else out.prediction)
                if pred_t.ndim == 3:
                    median = pred_t[..., pred_t.shape[-1] // 2]
                else:
                    median = pred_t
                median = median.cpu().numpy()
                # Recover decoder time positions for this batch.
                decoder_time_idx = x.get("decoder_time_idx")
                if decoder_time_idx is None:
                    # Older pytorch-forecasting versions: use target_scale.
                    # Fall back to ordered concatenation.
                    continue
                dec_idx = decoder_time_idx.cpu().numpy()
                for b in range(median.shape[0]):
                    for t_rel in range(median.shape[1]):
                        abs_t = int(dec_idx[b, t_rel])
                        shape_accum[abs_t] += median[b, t_rel]
                        shape_count[abs_t] += 1

        mask = shape_count > 0
        result = np.zeros(n_hours, dtype=np.float64)
        result[mask] = shape_accum[mask] / shape_count[mask]
        # Hours not covered (edge: first encoder_length) → fallback to 1.
        result[~mask] = 1.0
        # Drop the prefix, return just the year
        return result[enc:enc + 8760].astype(np.float32)

    def sensitivity_at_pixels(
        self,
        pixels: pd.DataFrame,
        temp_annual: np.ndarray,
        delta_t: float = 1.0,
        cfg: Optional[_SynthConfig] = None,
    ) -> np.ndarray:
        """Return η = (shape_warm - shape_base) / shape_base × 100 per pixel.

        Parameters
        ----------
        pixels : DataFrame with columns iso3, lat, lon, log_gdp_per_cap,
                 log_pop_density, urbanization (one row per pixel).
        temp_annual : ndarray (len(pixels),) in °C.
        delta_t : float warming applied in decoder window.
        cfg : optional _SynthConfig.

        Returns
        -------
        ndarray (len(pixels),) sensitivity in %/°C, averaged across the
        decoder prediction window.
        """
        df_base = self._build_pixel_batch_df(pixels, temp_annual,
                                             delta_t=0.0, cfg=cfg)
        shape_base = self._predict_decoder_shape(df_base)
        df_warm = self._build_pixel_batch_df(pixels, temp_annual,
                                             delta_t=delta_t, cfg=cfg)
        shape_warm = self._predict_decoder_shape(df_warm)

        # Average over the decoder window, then relative sensitivity
        base_mean = np.maximum(shape_base.mean(axis=1), 1e-6)
        warm_mean = shape_warm.mean(axis=1)
        eta = (warm_mean - base_mean) / base_mean * 100.0 / max(delta_t, 1e-6)
        return eta.astype(np.float32)
