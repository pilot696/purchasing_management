"""models.yaml 契约回归（Spec §2.5/§4.2）：

- reason 降级链必须跨厂商（评审意见 4b 的防回归单测）；
- 视觉角色必须是 json_schema 模式（只有支持约束解码的模型才可承担；当前网关 Qwen 只开 Anthropic 端点，故无 fallback）；
- 价格快照字段可被正确解析（OpenCode Go 2026-10-23 定价页）。
"""

from __future__ import annotations

from app.core.config import get_settings
from app.llm.config import load_llm_config


def test_reason_falls_back_to_a_different_provider() -> None:
    cfg = load_llm_config(get_settings().llm_config_file)
    reason = cfg.role("reason")
    assert reason.fallback, "reason 必须有降级链"
    primary_provider = reason.provider
    fallback_providers = {cfg.role(name).provider for name in reason.fallback}
    assert fallback_providers - {primary_provider}, "降级链里至少要有另一个厂商（跨厂商容灾）"


def test_vision_uses_json_schema() -> None:
    cfg = load_llm_config(get_settings().llm_config_file)
    vision = cfg.role("vision")
    assert vision.schema_mode == "json_schema"
    # 当前网关的 Qwen 视觉模型只提供 Anthropic /messages，OpenAI 兼容 provider 不可调用；
    # 适配器落地前不配置 fallback，避免"看起来有兜底、实际调不通"。
    assert vision.fallback == ()


def test_pricing_snapshot_parsed() -> None:
    cfg = load_llm_config(get_settings().llm_config_file)
    assert cfg.pricing["deepseek-v4.1-flash"] == {"in": 0.15, "out": 0.60}
    assert cfg.pricing["glm-5.3-flash"] == {"in": 0.15, "out": 0.50}
    # 官方未公开价格的模型不得被编造进 pricing（§13 O1）
    assert "deepseek-v4-pro" not in cfg.pricing


def test_roles_covered() -> None:
    cfg = load_llm_config(get_settings().llm_config_file)
    assert {"reason", "reason_small", "vision", "vision_cheap", "embed", "rerank"} <= set(cfg.roles)
