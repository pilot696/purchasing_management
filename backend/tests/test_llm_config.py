"""models.yaml 契约回归（Spec §2.5/§4.2）：

- 降级链必须跨厂商（评审意见 4b 的防回归单测）；
- qwen 视觉角色必须是 json_schema 模式（只有它支持约束解码）；
- 价格快照字段可被正确解析。
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


def test_vision_uses_json_schema_and_cross_vendor_fallback() -> None:
    cfg = load_llm_config(get_settings().llm_config_file)
    vision = cfg.role("vision")
    assert vision.schema_mode == "json_schema"
    fallback = [cfg.role(n) for n in vision.fallback]
    assert any(f.schema_mode == "json_schema" for f in fallback)  # 同厂商降配
    assert any(f.provider != vision.provider for f in fallback)  # 跨厂商兜底


def test_pricing_snapshot_parsed() -> None:
    cfg = load_llm_config(get_settings().llm_config_file)
    assert cfg.pricing["deepseek-flash"] == {"in": 0.15, "out": 0.60}
    # 官方未公开价格的模型不得被编造进 pricing（§13 O1）
    assert "qwen3.8-flash" not in cfg.pricing


def test_roles_covered() -> None:
    cfg = load_llm_config(get_settings().llm_config_file)
    assert {"reason", "reason_small", "vision", "vision_cheap", "vision_backup", "embed", "rerank"} <= set(
        cfg.roles
    )
