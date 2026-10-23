"""从 DB 加载规则集（按品类模板 code）。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import tables as t
from app.rules.engine import RuleDef


async def load_rules_for_category(session: AsyncSession, category_code: str) -> list[RuleDef]:
    rows = (
        await session.execute(
            select(t.Rule, t.CategoryTemplate)
            .join(t.CategoryTemplate, t.Rule.category_template_id == t.CategoryTemplate.id)
            .where(t.CategoryTemplate.category_code == category_code, t.Rule.enabled.is_(True))
            .order_by(t.Rule.code, t.Rule.version)
        )
    ).all()
    rules: list[RuleDef] = []
    seen: set[str] = set()
    for rule, _template in rows:
        if rule.code in seen:  # 多版本时取第一条（已按 code/version 排序，后续可加版本选择策略）
            continue
        seen.add(rule.code)
        rules.append(
            RuleDef(
                code=rule.code,
                type=rule.type,
                severity=rule.severity,
                field_path=rule.field_path,
                expression=dict(rule.expression or {}),
                message=rule.message_template,
                policy_ref=rule.policy_clause_ref,
                category_code=category_code,
                version=rule.version,
            )
        )
    return rules
