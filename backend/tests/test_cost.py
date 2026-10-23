"""成本记账口径（Spec §4.2）：cost_cny = (in/1e6*usd_in + out/1e6*usd_out) * fx。"""

from __future__ import annotations

import pytest

from app.llm.cost import UnknownModelPrice, compute_cost_cny, cost_for_model


def test_one_million_tokens_each_side() -> None:
    # 1M in @0.15 + 1M out @0.60 = 0.75 USD；fx=7.1 -> 5.325 CNY
    assert compute_cost_cny(1_000_000, 1_000_000, usd_in=0.15, usd_out=0.60, fx_usd_cny=7.1) == 5.325


def test_zero_tokens_is_free() -> None:
    assert compute_cost_cny(0, 0, usd_in=1.65, usd_out=4.951) == 0.0


def test_negative_tokens_rejected() -> None:
    with pytest.raises(ValueError):
        compute_cost_cny(-1, 0, usd_in=0.15, usd_out=0.60)


def test_unknown_model_price_is_explicit() -> None:
    with pytest.raises(UnknownModelPrice):
        cost_for_model("qwen3.8-flash", 1000, 1000, pricing={"deepseek-flash": {"in": 0.15, "out": 0.6}})


def test_cost_for_known_model() -> None:
    cost = cost_for_model(
        "deepseek-flash", 2000, 500, pricing={"deepseek-flash": {"in": 0.15, "out": 0.60}}, fx_usd_cny=7.1
    )
    expected_usd = 2000 / 1e6 * 0.15 + 500 / 1e6 * 0.60
    assert cost == pytest.approx(expected_usd * 7.1, abs=1e-9)
