"""M1 混合检索：词法（pg_trgm）+ 结构化（规格桶/别名）+ RRF 融合（Spec §5.3）。

说明（ADR-020）：向量 leg 需要 embedding provider（当前网关只有 complete，无 embed 角色）；
本层把第三路留成可注入参数，白天用"词法 + 结构化"两路 RRF 已有真实收益，接上 embedding 后直接加第三路即可。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

RRF_K = 60


@dataclass(frozen=True, slots=True)
class RetrievalHit:
    sku_code: str
    name: str
    spec_bucket: str
    score: float
    sources: tuple[str, ...]
    spec: dict[str, Any]
    ref_price_p50: float | None


def rrf_fuse(rankings: Sequence[Sequence[str]], *, k: int = RRF_K) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion：score(d) = Σ 1/(k + rank_i(d))，按分数降序、同分按 id 升序。"""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))


async def lexical_search(
    session: AsyncSession,
    query_text: str,
    *,
    category_code: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """pg_trgm 词法检索：中文短名用 similarity 排序（无需 zhparser）。"""
    category_clause = "AND category_code = :category" if category_code else ""
    sql = text(
        f"""
        SELECT sku_code, name, spec_bucket, spec, ref_price_p50,
               similarity(lower(name), lower(:query)) AS lex_score
        FROM sku_catalog
        WHERE TRUE {category_clause}
        ORDER BY lex_score DESC, sku_code
        LIMIT :limit
        """
    )
    rows = (
        await session.execute(
            sql,
            {"query": query_text, "category": category_code, "limit": limit},
        )
    ).mappings()
    return [
        {
            **dict(row),
            "ref_price_p50": float(row["ref_price_p50"]) if row["ref_price_p50"] is not None else None,
        }
        for row in rows
    ]


async def structured_search(
    session: AsyncSession,
    item_name: str,
    *,
    spec_bucket: str | None = None,
    category_code: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """结构化检索：规格桶精确命中、名称包含、别名字典命中（确定性）。"""
    category_clause = "AND category_code = :category" if category_code else ""
    bucket_clause = "OR spec_bucket = :bucket" if spec_bucket else ""
    sql = text(
        f"""
        SELECT sku_code, name, spec_bucket, spec, ref_price_p50,
               CASE
                 WHEN aliases @> jsonb_build_array(CAST(:item_name AS text)) THEN 3
                 WHEN strpos(lower(name), lower(:item_name)) > 0 THEN 2
                 WHEN spec_bucket = :bucket THEN 1
                 ELSE 0
               END AS struct_score
        FROM sku_catalog
        WHERE TRUE {category_clause}
          AND (
            aliases @> jsonb_build_array(CAST(:item_name AS text))
            OR strpos(lower(name), lower(:item_name)) > 0
            OR (COALESCE(CAST(:bucket AS text), '') <> '' AND spec_bucket = :bucket)
          )
        ORDER BY struct_score DESC, sku_code
        LIMIT :limit
        """
    )
    rows = (
        await session.execute(
            sql,
            {
                "item_name": item_name,
                "bucket": spec_bucket,
                "category": category_code,
                "limit": limit,
            },
        )
    ).mappings()
    return [
        {
            **dict(row),
            "ref_price_p50": float(row["ref_price_p50"]) if row["ref_price_p50"] is not None else None,
        }
        for row in rows
    ]


async def exact_search(
    session: AsyncSession,
    item_name: str,
    *,
    category_code: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """精确名/别名命中：名称完全相等或别名字典包含（最强的确定性信号）。"""
    category_clause = "AND category_code = :category" if category_code else ""
    sql = text(
        f"""
        SELECT sku_code, name, spec_bucket, spec, ref_price_p50
        FROM sku_catalog
        WHERE TRUE {category_clause}
          AND (
            lower(name) = lower(:item_name)
            OR aliases @> jsonb_build_array(CAST(:item_name AS text))
          )
        ORDER BY (lower(name) = lower(:item_name)) DESC, sku_code
        LIMIT :limit
        """
    )
    rows = (
        await session.execute(sql, {"item_name": item_name, "category": category_code, "limit": limit})
    ).mappings()
    return [
        {
            **dict(row),
            "ref_price_p50": float(row["ref_price_p50"]) if row["ref_price_p50"] is not None else None,
        }
        for row in rows
    ]


async def retrieve_sku_candidates(
    session: AsyncSession,
    *,
    item_name: str,
    spec_bucket: str | None,
    category_code: str | None,
    limit: int = 5,
) -> list[RetrievalHit]:
    """三路检索（词法 / 精确 / 结构化）+ RRF；返回统一候选。"""
    lexical = await lexical_search(session, item_name, category_code=category_code, limit=20)
    exact = await exact_search(session, item_name, category_code=category_code, limit=10)
    structured = await structured_search(
        session, item_name, spec_bucket=spec_bucket, category_code=category_code, limit=20
    )
    by_code: dict[str, dict[str, Any]] = {}
    sources: dict[str, set[str]] = {}
    for source_name, rows in (("lexical", lexical), ("exact", exact), ("structured", structured)):
        for row in rows:
            by_code.setdefault(row["sku_code"], row)
            sources.setdefault(row["sku_code"], set()).add(source_name)

    fused = rrf_fuse(
        [
            [row["sku_code"] for row in lexical],
            [row["sku_code"] for row in exact],
            [row["sku_code"] for row in structured],
        ]
    )
    hits: list[RetrievalHit] = []
    for sku_code, score in fused[:limit]:
        row = by_code[sku_code]
        hits.append(
            RetrievalHit(
                sku_code=sku_code,
                name=str(row["name"]),
                spec_bucket=str(row["spec_bucket"]),
                score=round(score, 6),
                sources=tuple(sorted(sources[sku_code])),
                spec=dict(row.get("spec") or {}),
                ref_price_p50=row.get("ref_price_p50"),
            )
        )
    return hits


def ambiguity_threshold(bucket: str | None) -> bool:
    """规格桶含通配符 = 规格信息不足，需要语义澄清（与 precheck 图共用）。"""
    return bool(bucket) and "*" in str(bucket)

