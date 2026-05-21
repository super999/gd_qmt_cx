from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

try:
    import lightgbm as lgb
except Exception as exc:  # pragma: no cover - explicit runtime guard
    raise RuntimeError(
        "LightGBM 未安装或无法导入。请先运行："
        "d:\\python_envs\\gd_qmt_env\\python.exe -m pip install lightgbm"
    ) from exc

from config import StrategyConfig


@dataclass
class ModelBundle:
    return_model: Any
    up_model: Optional[Any]
    risk_model: Optional[Any]
    trained_until: str
    train_samples: int


class WalkForwardModeler:
    def __init__(self, config: StrategyConfig) -> None:
        self.config = config

    def predict(self, dataset: pd.DataFrame) -> Tuple[pd.DataFrame, List[Dict[str, Any]], Optional[pd.DataFrame]]:
        trainable = self._clean_train_data(dataset)
        all_dates = sorted(dataset["trade_date"].dropna().unique())
        prediction_dates = [date for date in all_dates if date >= self.config.min_prediction_date]
        predictions: List[pd.DataFrame] = []
        train_logs: List[Dict[str, Any]] = []
        bundle: Optional[ModelBundle] = None
        last_feature_importance: Optional[pd.DataFrame] = None

        for prediction_index, trade_date in enumerate(prediction_dates, start=1):
            day_df = dataset[dataset["trade_date"] == trade_date]
            if day_df.empty:
                continue
            need_retrain = bundle is None or (prediction_index - 1) % self.config.retrain_every_n_days == 0
            if need_retrain:
                train_df = trainable[trainable["exit_date"] < trade_date].copy()
                if len(train_df) < self.config.min_train_samples:
                    continue
                bundle = self._train_models(train_df, trained_until=trade_date)
                train_logs.append(self._build_train_log(trade_date, train_df))
                last_feature_importance = self._feature_importance(bundle)

            if bundle is None:
                continue
            pred = self._predict_one_day(bundle, day_df)
            if not pred.empty:
                predictions.append(pred)

        if not predictions:
            return pd.DataFrame(), train_logs, last_feature_importance
        pred_df = pd.concat(predictions, ignore_index=True)
        return pred_df.sort_values(["trade_date", "pred_return_5d"], ascending=[True, False]), train_logs, last_feature_importance

    def _clean_train_data(self, df: pd.DataFrame) -> pd.DataFrame:
        needed = self.config.feature_cols + ["target_return_5d", "target_up_5d", "target_risk_5d"]
        return df[df["base_eligible"]].dropna(subset=needed).copy()

    def _feature_frame(self, df: pd.DataFrame) -> pd.DataFrame:
        return df[self.config.feature_cols].astype(float)

    def _train_models(self, train_df: pd.DataFrame, trained_until: str) -> ModelBundle:
        x_train = self._feature_frame(train_df)
        y_return = train_df["target_return_5d"].astype(float)
        y_up = train_df["target_up_5d"].astype(int)
        y_risk = train_df["target_risk_5d"].astype(int)
        return_model = self._build_regressor()
        return_model.fit(x_train, y_return)
        up_model = self._fit_classifier(x_train, y_up)
        risk_model = self._fit_classifier(x_train, y_risk)
        return ModelBundle(return_model, up_model, risk_model, trained_until, len(train_df))

    def _build_regressor(self) -> Any:
        return lgb.LGBMRegressor(
            n_estimators=160,
            learning_rate=0.05,
            num_leaves=31,
            subsample=0.85,
            colsample_bytree=0.85,
            random_state=self.config.random_state,
            n_jobs=-1,
            verbose=-1,
        )

    def _fit_classifier(self, x_train: pd.DataFrame, target: pd.Series) -> Optional[Any]:
        if target.nunique() < 2:
            return None
        model = lgb.LGBMClassifier(
            n_estimators=120,
            learning_rate=0.05,
            num_leaves=31,
            subsample=0.85,
            colsample_bytree=0.85,
            random_state=self.config.random_state,
            n_jobs=-1,
            verbose=-1,
        )
        model.fit(x_train, target)
        return model

    def _predict_one_day(self, bundle: ModelBundle, day_df: pd.DataFrame) -> pd.DataFrame:
        required = self.config.feature_cols + ["entry_date", "realized_next_open_return"]
        candidates = day_df[day_df["base_eligible"]].dropna(subset=required).copy()
        if candidates.empty:
            return pd.DataFrame()
        x_pred = self._feature_frame(candidates)
        candidates["pred_return_5d"] = bundle.return_model.predict(x_pred)
        candidates["pred_up_prob"] = bundle.up_model.predict_proba(x_pred)[:, 1] if bundle.up_model is not None else np.nan
        candidates["risk_prob"] = bundle.risk_model.predict_proba(x_pred)[:, 1] if bundle.risk_model is not None else 0.0
        candidates["risk_score"] = candidates["risk_prob"] * 100.0
        candidates["trained_until"] = bundle.trained_until
        candidates["train_samples"] = bundle.train_samples
        return candidates

    def _build_train_log(self, trade_date: str, train_df: pd.DataFrame) -> Dict[str, Any]:
        return {
            "prediction_date": trade_date,
            "train_samples": len(train_df),
            "train_start": train_df["trade_date"].min(),
            "train_end": train_df["trade_date"].max(),
            "positive_rate": float(train_df["target_up_5d"].mean()),
            "risk_rate": float(train_df["target_risk_5d"].mean()),
        }

    def _feature_importance(self, bundle: ModelBundle) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "feature": self.config.feature_cols,
                "feature_cn": [self.config.feature_labels[col] for col in self.config.feature_cols],
                "importance": bundle.return_model.feature_importances_,
            }
        ).sort_values("importance", ascending=False)
