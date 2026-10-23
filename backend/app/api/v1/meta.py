"""元数据接口：枚举字典（前后端共用，Spec §3.9）。"""

from __future__ import annotations

from fastapi import APIRouter

from app.observability.runevents import EVENT_TYPES

router = APIRouter(tags=["meta"])

ENUMS: dict[str, list[str]] = {
    "run.status": ["queued", "running", "awaiting_review", "succeeded", "failed", "cancelled"],
    "run.module": ["m1", "m2", "m3"],
    "finding.severity": ["high", "medium", "low", "info"],
    "finding.type": [
        "required",
        "range",
        "enum",
        "cross_field",
        "policy_ref",
        "stat_outlier",
        "ambiguity",
        "semantic_consistency",
        "question",
    ],
    "diff.type": [
        "price",
        "amount",
        "tax_rate",
        "qty_over",
        "qty_under",
        "date_overdue",
        "terms_conflict",
        "tail",
    ],
    "verdict.m1": ["pass", "needs_info", "block"],
    "verdict.m2": ["approve", "conditional", "reject", "frozen"],
    "overall.m3": ["pass", "release_with_note", "hold"],
    "match.method": ["L1_exact", "L2_bucket", "L3_semantic", "manual", "unmatched"],
    "review.kind": ["apply_patch", "submit_verdict", "release_or_hold"],
    "review.status": ["pending", "accepted", "rejected", "edited", "expired"],
    "demo.role": ["requester", "buyer", "finance", "admin"],
    "event.type": sorted(EVENT_TYPES),
}


@router.get("/meta/enums")
async def get_enums() -> dict[str, dict[str, list[str]]]:
    return {"enums": ENUMS}
