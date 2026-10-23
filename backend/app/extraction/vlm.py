"""扫描件/混合件的中文单据视觉抽取（Spec §7.2 的 VLM 分支）。

- `VisionExtractor` 是接缝：`FakeVisionExtractor` 用于零网络测试；`LLMVisionExtractor` 走模型网关的
  `vision` 角色（qwen3.8-max → glm-5.3-flash 跨厂商降级）。
- 结构化输出失败时把 Pydantic 的错误回填给模型重试（≤2 次，Spec §7.2 repair loop）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError

from app.llm.multimodal import build_vision_messages
from app.schemas.extraction import ContractExtract, DocType, InvoiceExtract, PoExtract

SCHEMAS: dict[str, type[BaseModel]] = {
    "invoice": InvoiceExtract,
    "po": PoExtract,
    "contract": ContractExtract,
}

INSTRUCTIONS: dict[str, str] = {
    "invoice": (
        "从这张中文增值税发票（或扫描件）中抽取结构化字段：发票代码/号码、开票日期、"
        "购买方与销售方名称及纳税人识别号、金额(未税)/税额/价税合计、税率、行项目（名称/规格/数量/单价/金额/税率）。"
        "看不清的字段填 null，不要反推金额。"
    ),
    "po": (
        "从这张采购订单中抽取：订单号、下单日期、供应商、币种、含税合计、行项目"
        "（行号/物料描述/规格/数量/未税单价/税率/未税金额/含税金额/承诺交期）。"
    ),
    "contract": (
        "从这张采购合同中抽取：合同编号、合同类型、甲方/乙方名称、合同金额、付款条款（账期天数、付款方式）、"
        "交付条款（交期天数、违约金条款）、关联订单号、行项目（标的物/数量/单价/税率）。"
    ),
}


class VisionExtractor(Protocol):
    name: str

    async def extract(
        self,
        *,
        images: list[bytes],
        doc_type: DocType,
        instruction: str,
        error_hint: str | None = None,
    ) -> dict[str, Any]: ...


class FakeVisionExtractor:
    """零网络测试替身：按调用顺序返回预置 payload（超出后重复最后一个）。"""

    name = "fake-vlm"

    def __init__(self, payload: dict[str, Any] | list[dict[str, Any]]) -> None:
        self._payloads = payload if isinstance(payload, list) else [payload]
        self.calls: list[dict[str, Any]] = []

    async def extract(
        self,
        *,
        images: list[bytes],
        doc_type: DocType,
        instruction: str,
        error_hint: str | None = None,
    ) -> dict[str, Any]:
        index = min(len(self.calls), len(self._payloads) - 1)
        self.calls.append({"doc_type": doc_type, "images": len(images), "error_hint": error_hint})
        return self._payloads[index]


class LLMVisionExtractor:
    name = "llm-vision"

    def __init__(self, router: Any, *, role: str = "vision") -> None:
        self._router = router
        self._role = role

    async def extract(
        self,
        *,
        images: list[bytes],
        doc_type: DocType,
        instruction: str,
        error_hint: str | None = None,
    ) -> dict[str, Any]:
        messages = build_vision_messages(instruction, images, error_hint=error_hint)
        result = await self._router.complete(
            role=self._role,
            messages=messages,
            schema=SCHEMAS[doc_type],
            step=f"extract.{doc_type}",
        )
        return json.loads(result.text)


@dataclass(slots=True)
class StructuredOutcome:
    payload: dict[str, Any] | None = None
    attempts: int = 0
    errors: list[str] = field(default_factory=list)


async def structured_vision_extract(
    vision: VisionExtractor,
    images: list[bytes],
    doc_type: DocType,
    *,
    max_attempts: int = 3,
) -> StructuredOutcome:
    """带 repair 的结构化抽取：Pydantic 校验失败 → 把错误回填模型重试。"""
    errors: list[str] = []
    hint: str | None = None
    for attempt in range(max_attempts):
        try:
            raw = await vision.extract(
                images=images,
                doc_type=doc_type,
                instruction=INSTRUCTIONS[doc_type],
                error_hint=hint,
            )
        except Exception as exc:  # noqa: BLE001 - 模型/网络错误也要进 repair 循环
            errors.append(f"attempt {attempt + 1}: {type(exc).__name__}: {exc}")
            hint = "上一次调用失败，请重新输出完整 JSON。"
            continue
        try:
            model = SCHEMAS[doc_type].model_validate(raw)
        except ValidationError as exc:
            summary = json.dumps(exc.errors(include_url=False), ensure_ascii=False)[:800]
            errors.append(f"attempt {attempt + 1}: {summary}")
            hint = summary
            continue
        return StructuredOutcome(payload=model.model_dump(mode="json"), attempts=attempt + 1, errors=errors)
    return StructuredOutcome(payload=None, attempts=max_attempts, errors=errors)
