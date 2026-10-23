"""成本换算（Spec §4.2 记账口径）：cost_cny = (in/1e6*usd_in + out/1e6*usd_out) * fx。"""

from __future__ import annotations

from dataclasses import dataclass


class UnknownModelPrice(KeyError):
    pass


@dataclass(slots=True, frozen=True)
class Price:
    usd_in: float
    usd_out: float


def compute_cost_cny(
    tokens_in: int,
    tokens_out: int,
    *,
    usd_in: float,
    usd_out: float,
    fx_usd_cny: float = 7.1,
) -> float:
    if tokens_in < 0 or tokens_out < 0:
        raise ValueError("tokens must be non-negative")
    usd = (tokens_in / 1_000_000) * usd_in + (tokens_out / 1_000_000) * usd_out
    return round(usd * fx_usd_cny, 6)


def cost_for_model(
    model: str,
    tokens_in: int,
    tokens_out: int,
    *,
    pricing: dict[str, dict[str, float]],
    fx_usd_cny: float = 7.1,
) -> float:
    """pricing 形如 {"deepseek-flash": {"in": 0.15, "out": 0.60}}；未配置价格视为不可记账。"""
    if model not in pricing:
        raise UnknownModelPrice(f"no price configured for model: {model}")
    p = pricing[model]
    return compute_cost_cny(tokens_in, tokens_out, usd_in=p["in"], usd_out=p["out"], fx_usd_cny=fx_usd_cny)
