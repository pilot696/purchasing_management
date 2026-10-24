"""models.yaml 加载：providers / roles / pricing（Spec §2.5、§4.2）。

角色是网关唯一的调用入口：业务代码写 `router.complete(role="reason", ...)`，
不直接引用厂商名，因此换厂商 = 改 yaml + .env。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)(?::-([^}]*))?\}")
_BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _dotenv_values() -> dict[str, str]:
    """把 backend/.env 合并进变量解析（Settings 读 .env 但不注入 os.environ）。

    历史坑：models.yaml 里 ${DEEPSEEK_API_KEY} 只能从 os.environ 解析，而 .env 只被
    pydantic-settings 读取 → 真实调用时 base_url/api_key 都被展开成空串。
    """
    values = dict(os.environ)
    env_file = _BACKEND_ROOT / ".env"
    if not env_file.exists():
        return values
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value[:1] == value[-1:] and value[:1] in {'"', "'"}:
            value = value[1:-1]
        values.setdefault(key, value)
    return values


def _expand_env(value: Any, env: dict[str, str] | None = None) -> Any:
    env = env or _dotenv_values()
    if isinstance(value, str):
        # 支持 ${VAR:-default}：未配置时用默认值，避免把模型名/URL 展开成空串
        return _ENV_PATTERN.sub(lambda m: env.get(m.group(1)) or (m.group(2) or ""), value)
    if isinstance(value, dict):
        return {k: _expand_env(v, env) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v, env) for v in value]
    return value


@dataclass(slots=True, frozen=True)
class ProviderConfig:
    name: str
    base_url: str
    api_key: str
    compat: str = "openai"
    extra_headers: dict[str, str] = field(default_factory=dict)
    session_header: str | None = None


@dataclass(slots=True, frozen=True)
class RoleConfig:
    role: str
    provider: str
    model: str
    schema_mode: str = "json_object"
    temperature: float = 0.0
    timeout_s: float = 60.0
    max_tokens: int = 2048
    fallback: tuple[str, ...] = ()
    dim: int | None = None
    batch: int | None = None


@dataclass(slots=True)
class LLMConfig:
    providers: dict[str, ProviderConfig] = field(default_factory=dict)
    roles: dict[str, RoleConfig] = field(default_factory=dict)
    pricing: dict[str, dict[str, float]] = field(default_factory=dict)
    fx_usd_cny: float = 7.1

    def role(self, name: str) -> RoleConfig:
        if name not in self.roles:
            raise KeyError(f"unknown llm role: {name}")
        return self.roles[name]


def parse_llm_config(raw: dict[str, Any]) -> LLMConfig:
    providers = {
        name: ProviderConfig(
            name=name,
            base_url=str(cfg.get("base_url", "")),
            api_key=str(cfg.get("api_key", "")),
            compat=str(cfg.get("compat", "openai")),
            extra_headers={str(k): str(v) for k, v in (cfg.get("extra_headers") or {}).items()},
            session_header=(str(cfg["session_header"]) if cfg.get("session_header") else None),
        )
        for name, cfg in (raw.get("providers") or {}).items()
    }
    roles: dict[str, RoleConfig] = {}
    for name, cfg in (raw.get("roles") or {}).items():
        roles[name] = RoleConfig(
            role=name,
            provider=str(cfg["provider"]),
            model=str(cfg["model"]),
            schema_mode=str(cfg.get("schema_mode", "json_object")),
            temperature=float(cfg.get("temperature", 0.0)),
            timeout_s=float(cfg.get("timeout_s", 60)),
            max_tokens=int(cfg.get("max_tokens", 2048)),
            fallback=tuple(cfg.get("fallback") or ()),
            dim=cfg.get("dim"),
            batch=cfg.get("batch"),
        )
    pricing: dict[str, dict[str, float]] = {}
    for model, price in (raw.get("pricing_usd_per_1m") or {}).items():
        if price.get("in") is None or price.get("out") is None:
            continue  # 未取价模型不参与成本记账（Spec §13 O1/O2）
        pricing[model] = {"in": float(price["in"]), "out": float(price["out"])}
    return LLMConfig(
        providers=providers,
        roles=roles,
        pricing=pricing,
        fx_usd_cny=float(raw.get("fx_usd_cny", 7.1)),
    )


@lru_cache(maxsize=8)
def _load_cached(path_str: str) -> LLMConfig:
    raw = yaml.safe_load(Path(path_str).read_text(encoding="utf-8")) or {}
    return parse_llm_config(_expand_env(raw))


def load_llm_config(path: Path) -> LLMConfig:
    return _load_cached(str(Path(path).resolve()))
