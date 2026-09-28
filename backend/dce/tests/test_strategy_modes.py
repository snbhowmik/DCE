"""T5.2: strategy modes validated from YAML; CUSTOM mode; wiring to optimizer params."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dce.optimize.inputs import ModeParams
from dce.runner import build_run_config
from dce.strategy import load_modes, resolve_mode
from dce.strategy.models import ModeConfig, validate_modes


def test_yaml_modes_validate() -> None:
    modes = load_modes()
    assert set(modes) == {"GROWTH", "STABILITY", "D2C_EXPANSION", "CUSTOM"}
    assert modes["STABILITY"].q_capacity == 0.15 and modes["STABILITY"].weights.pen == 3.0
    assert modes["D2C_EXPANSION"].onboarding_policy == "paused_unless_exceptional"
    assert modes["GROWTH"].onboarding_aqs_weights == "reach_heavy"


BASE = {
    "q_capacity": 0.5, "q_demand_b2b": 0.5, "weights": {"rev": 1.0}, "b2b_service_floor": 0.9,
    "res_gate": 0.0, "exploration_share": 0.1, "breach_threshold": 0.3,
}  # fmt: skip


@pytest.mark.parametrize(
    "bad",
    [
        {"q_capacity": 1.2},
        {"q_capacity": 0.0},
        {"b2b_service_floor": 1.5},
        {"exploration_share": -0.1},
        {"weights": {"rev": -1.0}},
        {"weights": {"revenue": 1.0}},  # unknown weight
        {"surprise": 1},  # unknown key
        {"onboarding_policy": "yolo"},
    ],
)
def test_bad_values_rejected(bad: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ModeConfig.model_validate(BASE | bad)


def test_unknown_aqs_profile_rejected() -> None:
    with pytest.raises(ValueError, match="AQS profile"):
        validate_modes({"X": BASE | {"onboarding_aqs_weights": "nope"}}, {"balanced"})


def test_custom_mode_overrides() -> None:
    m = resolve_mode("CUSTOM", {"weights": {"pen": 5.0}, "q_capacity": 0.2})
    assert m.weights.pen == 5.0 and m.weights.rev == 1.0 and m.q_capacity == 0.2
    with pytest.raises(ValidationError):
        resolve_mode("CUSTOM", {"q_capacity": 2.0})
    with pytest.raises(ValueError, match="CUSTOM"):
        resolve_mode("GROWTH", {"q_capacity": 0.2})
    with pytest.raises(KeyError):
        resolve_mode("TURBO")


def test_run_config_carries_validated_mode() -> None:
    run = build_run_config("CUSTOM", seed=1, mode_overrides={"weights": {"reach": 0.4}})
    assert run.mode_config["weights"]["reach"] == 0.4
    p = ModeParams.from_mode_config(run.mode, run.mode_config, run.app["optimize"])
    assert p.w_reach == 0.4 and p.name == "CUSTOM"
    s = ModeParams.from_mode_config("STABILITY", build_run_config("STABILITY").mode_config, {})
    assert (s.q_capacity, s.w_pen, s.b2b_service_floor) == (0.15, 3.0, 0.98)
