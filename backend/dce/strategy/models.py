"""Pydantic models for strategy modes (IDEATION §8, ARCH §7, PRD FR-15, T5.2)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Quantile = Field(gt=0.0, lt=1.0)
Share = Field(ge=0.0, le=1.0)


class Weights(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    rev: float = Field(1.0, ge=0)
    pen: float = Field(1.0, ge=0)
    gw: float = Field(0.3, ge=0)
    spend: float = Field(1.0, ge=0)
    reach: float = Field(0.0, ge=0)
    cust: float = Field(0.0, ge=0)


class ModeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    q_capacity: float = Quantile
    q_demand_b2b: float = Quantile
    q_demand_d2c: float = Field(0.5, gt=0.0, lt=1.0)
    weights: Weights
    b2b_service_floor: float = Share
    res_gate: float = Field(ge=-5.0, le=5.0, description="RES z-score threshold")
    exploration_share: float = Share
    breach_threshold: float = Quantile
    onboarding_aqs_weights: str | None = None
    onboarding_policy: Literal["normal", "paused_unless_exceptional"] = "normal"

    @field_validator("onboarding_aqs_weights")
    @classmethod
    def _profile_name(cls, v: str | None) -> str | None:
        if v is not None and not v.replace("_", "").isalnum():
            raise ValueError(f"invalid AQS profile name {v!r}")
        return v


def validate_modes(
    raw: dict[str, Any], aqs_profiles: set[str] | None = None
) -> dict[str, ModeConfig]:
    """Validate every mode; cross-check AQS profile names when the set is given."""
    modes = {name: ModeConfig.model_validate(cfg) for name, cfg in raw.items()}
    if aqs_profiles is not None:
        for name, m in modes.items():
            if m.onboarding_aqs_weights and m.onboarding_aqs_weights not in aqs_profiles:
                raise ValueError(
                    f"{name}: AQS profile {m.onboarding_aqs_weights!r} not in scoring.yaml "
                    f"({sorted(aqs_profiles)})"
                )
    return modes


def custom_mode(base: ModeConfig, overrides: dict[str, Any]) -> ModeConfig:
    """CUSTOM: user-set levers merged onto the CUSTOM base and re-validated."""
    data = base.model_dump()
    for k, v in overrides.items():
        if k == "weights":
            data["weights"] = {**data["weights"], **v}
        else:
            data[k] = v
    return ModeConfig.model_validate(data)
