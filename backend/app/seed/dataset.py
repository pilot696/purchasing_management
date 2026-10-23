"""确定性合成数据集构建（纯函数，不碰 DB / 文件）。

产物是一个嵌套 dict，`persist.py` 负责落库与写盘；单测用 `build_dataset()` 的哈希验证确定性。
字段命名与 ORM 列保持一一对应，减少映射代码。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from app.seed.config import (
    CATEGORIES,
    DEPARTMENTS,
    DIFF_PLAN,
    SUPPLIER_NAMES,
    SeedConfig,
    make_rng,
    money,
)

FISCAL_YEAR = 2026
BASE_DATE = date(2026, 10, 20)
TAX_RATE = 0.13
TOLERANCE_ROWS: tuple[dict[str, Any], ...] = (
    {"category_code": None, "diff_type": "price", "abs_tol": 1.0, "rel_tol": 0.005, "action": "hold", "priority": 10},
    {"category_code": None, "diff_type": "amount", "abs_tol": 1.0, "rel_tol": 0.001, "action": "hold", "priority": 10},
    {"category_code": None, "diff_type": "tax_rate", "abs_tol": 0.0, "rel_tol": 0.0, "action": "hold", "priority": 5},
    {"category_code": None, "diff_type": "qty_over", "abs_tol": 0.0, "rel_tol": 0.0, "action": "hold", "priority": 5},
    {"category_code": None, "diff_type": "qty_under", "abs_tol": 0.0, "rel_tol": 0.0, "action": "note", "priority": 50},
    {"category_code": None, "diff_type": "date_overdue", "abs_tol": 7.0, "rel_tol": 0.0, "action": "release_with_note", "priority": 40},
    {"category_code": None, "diff_type": "terms_conflict", "abs_tol": 0.0, "rel_tol": 0.0, "action": "hold", "priority": 20},
    {"category_code": None, "diff_type": "tail", "abs_tol": 0.05, "rel_tol": 0.0, "action": "auto_clear", "priority": 90},
)

RULE_TEMPLATES: tuple[dict[str, Any], ...] = (
    {
        "category_code": "IT_HW",
        "code": "IT-SPEC-MEM-01",
        "type": "required",
        "severity": "high",
        "field_path": "items[*].spec.memory_gb",
        "expression": {"required": ["items[0].spec.memory_gb"]},
        "message_template": "IT 硬件必须明确内存容量（历史标准配置 16GB）",
        "policy_clause_ref": "POL-IT-2025-03#4.2",
    },
    {
        "category_code": "IT_HW",
        "code": "IT-SPEC-DISK-01",
        "type": "required",
        "severity": "high",
        "field_path": "items[*].spec.storage_gb",
        "expression": {"required": ["items[0].spec.storage_gb"]},
        "message_template": "IT 硬件必须明确存储容量",
        "policy_clause_ref": "POL-IT-2025-03#4.3",
    },
    {
        "category_code": "IT_HW",
        "code": "IT-OWNER-01",
        "type": "required",
        "severity": "medium",
        "field_path": "items[*].spec.owner",
        "expression": {"required": ["items[0].owner"]},
        "message_template": "缺少使用人与归属成本中心",
        "policy_clause_ref": "POL-ADM-2024-11#2.1",
    },
    {
        "category_code": "IT_HW",
        "code": "IT-QTY-STOCK-01",
        "type": "stat_outlier",
        "severity": "medium",
        "field_path": "items[*].qty",
        "expression": {">": [{"var": "items[0].qty"}, 20]},
        "message_template": "申请数量超过 20 台，需说明批量采购合理性",
        "policy_clause_ref": "POL-IT-2025-03#6.1",
    },
    {
        "category_code": "IT_HW",
        "code": "IT-BUDGET-01",
        "type": "cross_field",
        "severity": "medium",
        "field_path": "estimated_amount",
        "expression": {">": [{"var": "estimated_amount"}, {"fn": "budget_available"}]},
        "message_template": "预估金额超出部门可用预算",
        "policy_clause_ref": "POL-FIN-2025-01#3.2",
    },
    {
        "category_code": "SAAS",
        "code": "SAAS-PERIOD-01",
        "type": "required",
        "severity": "high",
        "field_path": "items[*].spec.period_months",
        "expression": {"required": ["items[0].spec.period_months"]},
        "message_template": "SaaS 订阅必须明确订阅周期（月）",
        "policy_clause_ref": "POL-IT-2025-05#1.4",
    },
    {
        "category_code": "SAAS",
        "code": "SAAS-SECURITY-01",
        "type": "policy_ref",
        "severity": "high",
        "field_path": "items[*].spec.data_scope",
        "expression": {"in": [{"var": "items[0].spec.data_scope"}, ["客户数据", "财务数据"]]},
        "message_template": "涉及客户/财务数据的 SaaS 必须提供等保三级或 ISO27001",
        "policy_clause_ref": "POL-SEC-2025-02#5.1",
    },
    {
        "category_code": "ADMIN",
        "code": "ADM-UNIT-01",
        "type": "enum",
        "severity": "medium",
        "field_path": "items[*].unit",
        "expression": {"not": {"in": [{"var": "items[0].unit"}, ["个", "台", "套", "箱", "件"]]}},
        "message_template": "计量单位不合法，需使用标准单位",
        "policy_clause_ref": "POL-ADM-2024-11#1.3",
    },
    {
        "category_code": "ADMIN",
        "code": "ADM-BUDGET-01",
        "type": "cross_field",
        "severity": "medium",
        "field_path": "estimated_amount",
        "expression": {">": [{"var": "estimated_amount"}, {"fn": "budget_available"}]},
        "message_template": "预估金额超出部门可用预算",
        "policy_clause_ref": "POL-FIN-2025-01#3.2",
    },
    {
        "category_code": "ADMIN",
        "code": "ADM-POLICY-01",
        "type": "policy_ref",
        "severity": "high",
        "field_path": "items[*].spec.data_scope",
        "expression": {"in": [{"var": "items[0].spec.data_scope"}, ["客户数据", "财务数据"]]},
        "message_template": "涉及敏感数据的行政采购需走信息安全审批",
        "policy_clause_ref": "POL-SEC-2025-02#5.2",
    },
    {
        "category_code": "MKT_OUTSOURCE",
        "code": "MKT-ACCEPT-01",
        "type": "required",
        "severity": "high",
        "field_path": "items[*].spec.acceptance_criteria",
        "expression": {"required": ["items[0].spec.acceptance_criteria"]},
        "message_template": "市场/外包服务必须写明可验收的交付物标准",
        "policy_clause_ref": "POL-MKT-2025-04#2.2",
    },
    {
        "category_code": "MKT_OUTSOURCE",
        "code": "MKT-SUPPLIER-01",
        "type": "policy_ref",
        "severity": "high",
        "field_path": "items[*].spec.related_party",
        "expression": {"==": [{"var": "items[0].spec.related_party"}, True]},
        "message_template": "关联方交易需额外披露并走专项审批",
        "policy_clause_ref": "POL-CMP-2025-01#7.4",
    },
    # 跨品类通用规则：数量异常 / 超预算（gold 的 cross_field、stat_outlier 对所有品类生效）
    {
        "category_code": "SAAS",
        "code": "SAAS-BUDGET-01",
        "type": "cross_field",
        "severity": "medium",
        "field_path": "estimated_amount",
        "expression": {">": [{"var": "estimated_amount"}, {"fn": "budget_available"}]},
        "message_template": "预估金额超出部门可用预算",
        "policy_clause_ref": "POL-FIN-2025-01#3.2",
    },
    {
        "category_code": "MKT_OUTSOURCE",
        "code": "MKT-BUDGET-01",
        "type": "cross_field",
        "severity": "medium",
        "field_path": "estimated_amount",
        "expression": {">": [{"var": "estimated_amount"}, {"fn": "budget_available"}]},
        "message_template": "预估金额超出部门可用预算",
        "policy_clause_ref": "POL-FIN-2025-01#3.2",
    },
    {
        "category_code": "SAAS",
        "code": "SAAS-QTY-01",
        "type": "stat_outlier",
        "severity": "medium",
        "field_path": "items[*].qty",
        "expression": {">": [{"var": "items[0].qty"}, 20]},
        "message_template": "申请数量超过批量阈值，需说明合理性",
        "policy_clause_ref": "POL-IT-2025-03#6.1",
    },
    {
        "category_code": "ADMIN",
        "code": "ADM-QTY-01",
        "type": "stat_outlier",
        "severity": "medium",
        "field_path": "items[*].qty",
        "expression": {">": [{"var": "items[0].qty"}, 20]},
        "message_template": "申请数量超过批量阈值，需说明合理性",
        "policy_clause_ref": "POL-IT-2025-03#6.1",
    },
    {
        "category_code": "MKT_OUTSOURCE",
        "code": "MKT-QTY-01",
        "type": "stat_outlier",
        "severity": "medium",
        "field_path": "items[*].qty",
        "expression": {">": [{"var": "items[0].qty"}, 20]},
        "message_template": "申请数量超过批量阈值，需说明合理性",
        "policy_clause_ref": "POL-IT-2025-03#6.1",
    },
)

SKU_SPEC_POOL: dict[str, list[dict[str, Any]]] = {
    "IT_HW": [
        {"memory_gb": 16, "storage_gb": 512, "cpu": "i5-1345U", "size_inch": 14},
        {"memory_gb": 32, "storage_gb": 1024, "cpu": "i7-1365U", "size_inch": 14},
        {"memory_gb": 16, "storage_gb": 512, "cpu": "M3", "size_inch": 13},
        {"memory_gb": 64, "storage_gb": 2048, "cpu": "Xeon-Silver", "size_inch": 0},
    ],
    "SAAS": [
        {"period_months": 12, "seats": 20, "data_scope": "内部数据"},
        {"period_months": 12, "seats": 50, "data_scope": "内部数据"},
        {"period_months": 6, "seats": 10, "data_scope": "内部数据"},
    ],
    "ADMIN": [
        {"material": "环保复印纸", "package": "A4/500张"},
        {"material": "人体工学椅", "package": "1把"},
        {"material": "白板笔", "package": "12支/盒"},
    ],
    "MKT_OUTSOURCE": [
        {"acceptance_criteria": "活动到场人数≥200，物料交付齐全", "duration_days": 3},
        {"acceptance_criteria": "月度报告 + 通过验收评审", "duration_days": 30},
        {"acceptance_criteria": "交付源代码并通过安全扫描", "duration_days": 60},
    ],
}

SKU_NAME_POOL: dict[str, list[str]] = {
    "IT_HW": [
        "ThinkPad T14 Gen5 笔记本",
        "MacBook Pro 14 笔记本",
        "MacBook Air 13 笔记本",
        "Dell PowerEdge R750 服务器",
        "Dell U2723QE 显示器",
        "华为 MateView 显示器",
        "罗技 MX Master 3S 鼠标",
        "机械革命 办公台式机",
        "群晖 DS923+ 存储",
        "H3C S5130 交换机",
    ],
    "SAAS": [
        "飞书企业版订阅",
        "Adobe Creative Cloud 订阅",
        "Figma 团队版订阅",
        "GitLab 私有化订阅",
        "Salesforce 销售云订阅",
        "钉钉专业版订阅",
        "Zoom 企业版订阅",
        "Notion 团队版订阅",
    ],
    "ADMIN": [
        "A4 复印纸",
        "人体工学办公椅",
        "白板笔套装",
        "文件柜",
        "会议室投屏器",
        "茶水间耗材包",
        "工位显示器支架",
        "打印硒鼓",
    ],
    "MKT_OUTSOURCE": [
        "产品发布会会务服务",
        "品牌短视频拍摄",
        "社媒代运营服务",
        "市场调研外包",
        "前端开发外包",
        "客服外包服务",
        "展会搭建服务",
        "宣传册设计",
    ],
}


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _build_master(rng, cfg: SeedConfig) -> dict[str, Any]:
    departments = [
        {
            "name": name,
            "cost_center": cc,
            "company_code": "1000",
        }
        for name, cc in DEPARTMENTS
    ]

    users: list[dict[str, Any]] = []
    for idx, (name, cc) in enumerate(DEPARTMENTS):
        users.append(
            {
                "name": f"{name}申请人{idx + 1}",
                "email": f"requester{idx + 1}@example.com",
                "role": "requester",
                "department_cost_center": cc,
            }
        )
    for i, role in enumerate(("buyer", "buyer", "finance", "finance", "admin")):
        users.append(
            {
                "name": f"{role}-{i + 1}",
                "email": f"{role}{i + 1}@example.com",
                "role": role,
                "department_cost_center": "CC-ADM-002" if role in {"buyer", "admin"} else "CC-FIN-004",
            }
        )

    budgets: list[dict[str, Any]] = []
    for dept in departments:
        for code, _, _ in CATEGORIES:
            total = rng.choice((200_000, 300_000, 500_000, 800_000))
            used = int(total * rng.uniform(0.45, 0.85))
            committed = int(total * rng.uniform(0.05, 0.20))
            # 让 3 个部门的预算紧张（可用额 < 预估），用于超预算样本
            if dept["cost_center"] in {"CC-IT-001", "CC-MKT-003", "CC-RD-006"} and code == "IT_HW":
                used = int(total * 0.93)
                committed = int(total * 0.06)
            budgets.append(
                {
                    "department_cost_center": dept["cost_center"],
                    "fiscal_year": FISCAL_YEAR,
                    "category_code": code,
                    "amount_total": total,
                    "amount_committed": committed,
                    "amount_used": used,
                    "currency": "CNY",
                }
            )

    categories = [
        {
            "category_code": code,
            "name": name,
            "version": 1,
            "required_slots": {
                "IT_HW": ["spec.memory_gb", "spec.storage_gb", "owner", "cost_center"],
                "SAAS": ["spec.period_months", "spec.seats", "data_scope"],
                "ADMIN": ["unit", "delivery_address"],
                "MKT_OUTSOURCE": ["spec.acceptance_criteria", "owner", "cost_center"],
            }[code],
            "unit_policy": {"units": ["台", "套", "个", "年", "人天"]},
            "budget_subject": f"SUBJ-{code}",
            "acceptance_points": {
                "IT_HW": ["SN 一致", "外观无损伤", "开机自检通过"],
                "SAAS": ["账号开通", "SLA 生效"],
                "ADMIN": ["数量一致", "无破损"],
                "MKT_OUTSOURCE": ["交付物齐全", "验收评审通过"],
            }[code],
            "requires_gr": requires_gr,
        }
        for code, name, requires_gr in CATEGORIES
    ]

    rules: list[dict[str, Any]] = []
    for rule in RULE_TEMPLATES:
        rules.append({**rule, "version": 1, "enabled": True})

    # 五维权重（Spec §6.3）
    weight_table = {
        "IT_HW": (0.25, 0.30, 0.20, 0.15, 0.10),
        "SAAS": (0.20, 0.15, 0.20, 0.30, 0.15),
        "ADMIN": (0.35, 0.25, 0.20, 0.15, 0.05),
        "MKT_OUTSOURCE": (0.30, 0.20, 0.20, 0.20, 0.10),
    }
    dims = ("price", "delivery", "quality", "service", "compliance")
    supplier_weights = [
        {"category_code": code, "dimension": dim, "weight": weight, "version": "v1"}
        for code, weights in weight_table.items()
        for dim, weight in zip(dims, weights, strict=True)
    ]

    suppliers: list[dict[str, Any]] = []
    certs: list[dict[str, Any]] = []
    gate_suppliers = {3, 7, 11, 15, 19, 23}
    blacklisted = {5, 13, 27}
    expiring = {2, 9, 17, 25}
    cold_start = set(range(22, 30))
    for idx in range(cfg.supplier_count):
        code = f"SUP-{idx + 1:03d}"
        scope = [rng.choice([c for c, _, _ in CATEGORIES]) for _ in range(rng.randint(1, 3))]
        scope = sorted(set(scope))
        status = "blacklisted" if idx in blacklisted else ("pending" if idx in cold_start else "active")
        risk_flags: dict[str, Any] = {"negative_credit": False}
        if idx in gate_suppliers:
            risk_flags = {
                "license_invalid": idx == 3,
                "dishonest_judgment": idx == 7,
                "tax_abnormal": idx == 11,
                "major_default": idx == 15,
                "scope_mismatch": idx == 19,
                "security_cert_missing": idx == 23,
            }
        suppliers.append(
            {
                "code": code,
                "name": SUPPLIER_NAMES[idx],
                "unified_social_credit_code": f"91110{idx:03d}MA{idx:02d}X{idx:03d}Y",
                "category_scope": scope,
                "status": status,
                "risk_flags": risk_flags,
                "onboarded_at": None if idx in cold_start else datetime(2024, 1, 1) + timedelta(days=idx * 23),
                "is_cold_start": idx in cold_start,
            }
        )
        cert_types = ["营业执照", "开户许可证"]
        if "SAAS" in scope:
            cert_types.append("ISO27001")
        for cert_type in cert_types:
            # 基础有效期放远：只有 expiring 集合刻意造"临期"，避免随机产生大量已过期证照（会误触发 G1）
            valid_to = date(2027, 12, 31) + timedelta(days=rng.randint(30, 900))
            if idx in expiring:
                valid_to = date(2026, 11, 1)  # 30 天内到期
            certs.append(
                {
                    "supplier_code": code,
                    "cert_type": cert_type,
                    "cert_no": f"{cert_type[:2]}-{idx:03d}-{rng.randint(1000, 9999)}",
                    "issuer": "市场监督管理局",
                    "valid_from": date(2023, 1, 1),
                    "valid_to": valid_to,
                }
            )

    skus: list[dict[str, Any]] = []
    per_category = cfg.sku_count // len(CATEGORIES)
    price_base = {"IT_HW": 6500.0, "SAAS": 38000.0, "ADMIN": 260.0, "MKT_OUTSOURCE": 52000.0}
    for code, _, _ in CATEGORIES:
        spec_pool = SKU_SPEC_POOL[code]
        name_pool = SKU_NAME_POOL[code]
        for i in range(per_category):
            # 规格跟随"品名"而不是全局索引：同名 SKU 的规格一致（否则目录出现同名不同配，检索评测失真）
            name_index = i % len(name_pool)
            spec = dict(spec_pool[name_index % len(spec_pool)])
            name = name_pool[name_index]
            if code == "IT_HW":
                bucket = f"laptop/{spec['memory_gb']}g/{spec['storage_gb']}g"
                if spec.get("size_inch", 0) == 0:
                    bucket = f"server/{spec['memory_gb']}g/{spec['storage_gb']}g"
            elif code == "SAAS":
                bucket = f"saas/{spec['seats']}seats/{spec['period_months']}m"
            elif code == "ADMIN":
                bucket = f"admin/{spec['material']}"
            else:
                bucket = f"outsource/{spec['duration_days']}d"
            base = price_base[code] * (0.8 + 0.1 * (i % 5))
            skus.append(
                {
                    "category_code": code,
                    "sku_code": f"SKU-{code}-{i + 1:03d}",
                    "name": name,
                    "brand": name.split(" ")[0],
                    "spec": spec,
                    "spec_bucket": bucket,
                    "unit": "年" if code == "SAAS" else ("人天" if code == "MKT_OUTSOURCE" else "台"),
                    "ref_price_p50": money(base),
                    "aliases": [name.replace(" ", ""), f"{name.split(' ')[0]}同款"],
                }
            )

    policies: list[dict[str, Any]] = []
    clause_titles = [
        "采购申请完整性要求",
        "预算与审批权限",
        "供应商准入资质要求",
        "验收与付款条款",
        "信息安全与数据合规",
        "关联方与利益冲突",
        "数量异常与重复采购",
        "分批交货与开票规则",
        "合同变更与违约处理",
        "发票开具与税率要求",
    ]
    for code, name, _ in CATEGORIES:
        for i in range(15):  # 4 品类 × 15 = 60 条，与 Spec §10.2 对齐
            title = clause_titles[i % len(clause_titles)]
            policies.append(
                {
                    "category_code": code,
                    "clause_no": f"POL-{code}-2025-{i + 1:02d}#{i % 5 + 1}.{i % 3 + 1}",
                    "title": f"{name}{title}" + ("" if i < len(clause_titles) else f"（补充 {i // len(clause_titles) + 1}）"),
                    "content": (
                        f"{name}品类{title}：申请人须提供完整规格与使用信息；"
                        f"采购员须核对预算、库存与历史价格；超过标准配置需附说明。"
                        f"（条款 {i + 1}，生效日期 2025-01-01）"
                    ),
                    "effective_from": date(2025, 1, 1),
                }
            )

    # 历史成交行（价格基准来源）：12 个月跨度，保证每个 spec_bucket ≥ 5 条
    po_history: list[dict[str, Any]] = []
    sku_by_bucket: dict[str, dict[str, Any]] = {}
    for sku in skus:
        sku_by_bucket.setdefault(sku["spec_bucket"], sku)
    buckets = list(sku_by_bucket)
    suppliers_by_cat = {c: [s for s in suppliers if c in s["category_scope"]] for c, _, _ in CATEGORIES}
    for i in range(cfg.po_history_count):
        bucket = buckets[i % len(buckets)]
        sku = sku_by_bucket[bucket]
        category = sku["category_code"]
        supplier_pool = suppliers_by_cat[category] or suppliers
        supplier = supplier_pool[i % len(supplier_pool)]
        months_ago = rng.randint(0, 12)
        order_date = BASE_DATE - timedelta(days=months_ago * 30 + rng.randint(0, 20))
        price = sku["ref_price_p50"] * rng.lognormvariate(0, 0.10) * (1 + 0.02 * (12 - months_ago))
        po_history.append(
            {
                "po_no": f"PO-{order_date.year}-H{i + 1:04d}",
                "line_no": 1,
                "item_name_raw": sku["name"],
                "spec_text": " / ".join(f"{k}={v}" for k, v in sku["spec"].items()),
                "spec_bucket": bucket,
                "supplier_code": supplier["code"],
                "unit_price_tax_excl": money(price),
                "tax_rate": TAX_RATE,
                "qty": rng.choice((1, 2, 5, 10)),
                "order_date": order_date,
                "category_code": category,
            }
        )

    inventory = [
        {
            "category_code": "IT_HW",
            "spec_bucket": sku["spec_bucket"],
            "sku_code": sku["sku_code"],
            "qty_on_hand": rng.randint(0, 8),
            "qty_reserved": rng.randint(0, 2),
            "warehouse": rng.choice(("总部 IT 仓库", "分部备件仓")),
        }
        for sku in skus
        if sku["category_code"] == "IT_HW"
    ][: cfg.inventory_count]

    tickets: list[dict[str, Any]] = []
    active_suppliers = [s for s in suppliers if s["status"] != "blacklisted"]
    for i in range(cfg.service_ticket_count):
        supplier = active_suppliers[i % len(active_suppliers)]
        opened = datetime(2025, 10, 1) + timedelta(days=rng.randint(0, 380), hours=rng.randint(0, 23))
        resp_hours = rng.choice((1, 2, 4, 8, 12, 24, 48))
        resolve_hours = resp_hours + rng.choice((2, 6, 24, 72))
        tickets.append(
            {
                "supplier_code": supplier["code"],
                "po_no": None,
                "severity": rng.choice(("low", "medium", "high")),
                "opened_at": opened,
                "first_response_at": opened + timedelta(hours=resp_hours),
                "resolved_at": opened + timedelta(hours=resolve_hours),
                "escalated": rng.random() < 0.12,
                "reopened": rng.random() < 0.08,
                "note": "售后工单（合成数据）",
            }
        )

    return {
        "departments": departments,
        "users": users,
        "budgets": budgets,
        "categories": categories,
        "rules": rules,
        "supplier_weights": supplier_weights,
        "suppliers": suppliers,
        "certs": certs,
        "skus": skus,
        "policies": policies,
        "po_history": po_history,
        "inventory": inventory,
        "tickets": tickets,
        "tolerance": [dict(row) for row in TOLERANCE_ROWS],
    }


def _line(qty: float, unit_price: float, tax_rate: float) -> tuple[float, float, float]:
    amount = money(qty * unit_price)
    tax = money(amount * tax_rate)
    return amount, tax, money(amount + tax)


def _price_for(sku: dict[str, Any], supplier: dict[str, Any], rng) -> float:
    quality_premium = 1.03 if supplier["status"] == "active" and not supplier["risk_flags"].get("major_default") else 1.0
    return money(sku["ref_price_p50"] * quality_premium * rng.uniform(0.95, 1.06))


def _build_prs(rng, cfg: SeedConfig, master: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """M1 的 PR 样本 + gold（W2 使用，数据工厂一次生成）。"""
    skus_by_cat: dict[str, list[dict[str, Any]]] = {c: [] for c, _, _ in CATEGORIES}
    for sku in master["skus"]:
        skus_by_cat[sku["category_code"]].append(sku)
    budget_by_key = {(b["department_cost_center"], b["category_code"]): b for b in master["budgets"]}

    prs: list[dict[str, Any]] = []
    gold: list[dict[str, Any]] = []
    anomaly_cycle = ["missing_field", "ambiguity", "over_budget", "qty_outlier", "clean", "clean", "clean", "clean"]
    for i in range(cfg.pr_count):
        category = CATEGORIES[i % len(CATEGORIES)][0]
        sku = skus_by_cat[category][i % len(skus_by_cat[category])]
        dept = master["departments"][i % len(master["departments"])]
        # 每 20 条插入 1 条"硬政策违规"（block），保证评测集有 block 样本
        anomaly = "policy_block" if i % 20 == 10 else anomaly_cycle[i % len(anomaly_cycle)]
        qty = rng.choice((1, 2)) if anomaly == "clean" else rng.choice((1, 2, 3, 5, 10, 15))
        spec: dict[str, Any] = dict(sku["spec"])
        owner: str | None = f"user{i % 6 + 1}"
        cost_center: str | None = dept["cost_center"]
        item_name = sku["name"]
        title = f"采购{sku['name']} {qty} {sku['unit']}"
        missing: list[str] = []
        ambiguity = False
        policy_block = anomaly == "policy_block"
        if anomaly == "missing_field":
            if category == "IT_HW":
                spec.pop("memory_gb", None)
                missing.append("items.0.spec.memory_gb")
            elif category == "SAAS":
                spec.pop("period_months", None)
                missing.append("items.0.spec.period_months")
            else:
                owner = None
                missing.append("items.0.spec.owner")
        if anomaly == "ambiguity":
            item_name = "办公电脑" if category == "IT_HW" else "会议服务"
            spec = {}
            ambiguity = True
        if anomaly == "qty_outlier":
            qty = rng.choice((60, 80))
        if policy_block:
            if category == "MKT_OUTSOURCE":
                spec["related_party"] = True
            else:
                spec["data_scope"] = "客户数据"
        # pr_item 没有 owner/cost_center 独立列（Spec §3.4），统一放进 spec 槽位，规则路径同步为 items[*].spec.owner
        if owner is not None:
            spec["owner"] = owner
        if cost_center is not None:
            spec["cost_center"] = cost_center
        unit_price = money(sku["ref_price_p50"] * rng.uniform(0.95, 1.08))
        estimated = money(qty * unit_price)
        budget = budget_by_key[(dept["cost_center"], category)]
        available = budget["amount_total"] - budget["amount_used"] - budget["amount_committed"]
        # 超预算必须由"实际可用额"推导，否则 clean 样本会被真实引擎判成 needs_info，gold 失真
        over_budget = estimated > available
        if anomaly == "over_budget":
            over_budget = True
            # 金额调高但保留正常数量（否则会同时触发"数量异常"规则，产生 gold 未建模的 finding）
            estimated = money(available * 1.4) if available > 0 else 250_000.0

        pr_no = f"PR-{FISCAL_YEAR}-{i + 1:04d}"
        prs.append(
            {
                "pr_no": pr_no,
                "requester_email": f"requester{i % 6 + 1}@example.com",
                "department_cost_center": dept["cost_center"],
                "category_code": category,
                "title": title,
                "needed_by": BASE_DATE + timedelta(days=rng.randint(5, 40)),
                "estimated_amount": estimated,
                "currency": "CNY",
                "status": "submitted",
                "created_at": datetime(2026, 9, 1) + timedelta(days=i),
                "items": [
                    {
                        "line_no": 1,
                        "item_name_raw": item_name,
                        "spec": spec,
                        "qty": qty,
                        "unit": sku["unit"],
                        "unit_price_est": unit_price,
                        "amount_est": estimated,
                        "sku_code": sku["sku_code"],
                        "spec_bucket": sku["spec_bucket"],
                    }
                ],
            }
        )
        findings: list[dict[str, Any]] = []
        for path in missing:
            # owner 属于"建议补全"级别（规则 IT-OWNER-01 = medium），其余必填字段为 high
            severity = "medium" if path.endswith("spec.owner") else "high"
            findings.append({"type": "required", "field": f"/{path.replace('.', '/')}", "severity": severity})
        if ambiguity:
            findings.append({"type": "ambiguity", "field": "/items/0/item_name", "severity": "high"})
        if over_budget:
            findings.append({"type": "cross_field", "field": "/estimated_amount", "severity": "medium"})
        if anomaly == "qty_outlier":
            findings.append({"type": "stat_outlier", "field": "/items/0/qty", "severity": "medium"})
        if policy_block:
            findings.append(
                {
                    "type": "policy_ref",
                    "field": "/items/0/spec/related_party" if category == "MKT_OUTSOURCE" else "/items/0/spec/data_scope",
                    "severity": "high",
                }
            )
        verdict = "block" if policy_block else ("needs_info" if findings else "pass")
        gold.append(
            {
                "case_id": f"m1-{i + 1:04d}",
                "pr_no": pr_no,
                "category_code": category,
                "anomaly": anomaly,
                "expected_verdict": verdict,
                "expected_findings": findings,
                "expected_missing_fields": [f"/{p.replace('.', '/')}" for p in missing],
                "expected_ambiguity_fields": ["/items/0/item_name"] if ambiguity else [],
                "expected_over_budget": over_budget,
                "expected_policy_block": policy_block,
            }
        )
    return prs, gold


def _build_doc_groups(rng, cfg: SeedConfig, master: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """M3 的合同/PO/收货/发票样本 + gold（每类差异 ≥10 正样本）。"""
    skus_by_cat: dict[str, list[dict[str, Any]]] = {c: [] for c, _, _ in CATEGORIES}
    for sku in master["skus"]:
        skus_by_cat[sku["category_code"]].append(sku)
    supplier_by_cat = {c: [s for s in master["suppliers"] if c in s["category_scope"] and s["status"] != "blacklisted"] for c, _, _ in CATEGORIES}
    requires_gr = {c: gr for c, _, gr in CATEGORIES}

    # 把 DIFF_PLAN 展开成槽位并打散；每个槽位都落到"尚未出现该类型"的组，保证每类精确达标（Spec §10.3）
    slots: list[str] = []
    for diff_type, count in DIFF_PLAN:
        slots.extend([diff_type] * count)
    rng.shuffle(slots)
    anomaly_group_count = int(cfg.doc_groups * cfg.anomaly_ratio)  # 45% ≈ 36 组
    anomaly_group_ids = list(range(cfg.doc_groups))[:anomaly_group_count]
    rng.shuffle(anomaly_group_ids)
    assignments: dict[int, list[str]] = {}
    cursor = 0
    for slot in slots:
        for _ in range(len(anomaly_group_ids)):
            group_no = anomaly_group_ids[cursor % len(anomaly_group_ids)]
            cursor += 1
            if slot not in assignments.get(group_no, []):
                assignments.setdefault(group_no, []).append(slot)
                break
        else:  # pragma: no cover - 类型数 <= 组数时不可能走到这里
            raise RuntimeError(f"无法为差异类型 {slot} 找到可分配的组")
    groups: list[dict[str, Any]] = []
    gold: list[dict[str, Any]] = []
    # 扫描件按"精确配额 + 打散"分配，避免随机流漂移导致比例偏离 Spec（15%）
    scanned_group_count = max(1, round(cfg.doc_groups * cfg.scanned_ratio))
    scanned_group_ids = set(rng.sample(range(cfg.doc_groups), scanned_group_count))

    for i in range(cfg.doc_groups):
        category = "IT_HW" if i % 5 != 4 else CATEGORIES[i % len(CATEGORIES)][0]
        sku_pool = skus_by_cat[category]
        sku = sku_pool[i % len(sku_pool)]
        supplier_pool = supplier_by_cat[category] or master["suppliers"][:5]
        supplier = supplier_pool[i % len(supplier_pool)]
        qty_ordered = rng.choice((2, 5, 10, 15))
        unit_price = _price_for(sku, supplier, rng)
        amount, tax, total = _line(qty_ordered, unit_price, TAX_RATE)
        promised = BASE_DATE + timedelta(days=rng.randint(10, 45))
        ordered_at = datetime(2026, 7, 1) + timedelta(days=i * 2)
        po_no = f"PO-{FISCAL_YEAR}-{i + 1:04d}"
        contract_no = f"CT-{FISCAL_YEAR}-{i + 1:04d}"
        case_id = f"m3-{i + 1:04d}"

        anomalies: list[dict[str, Any]] = []
        diff_types: list[str] = list(assignments.get(i, []))

        # ---- 收货（含分批/不足）；SaaS/服务类无 GR（requires_gr=False）
        partial = rng.random() < cfg.partial_invoice_ratio and requires_gr[category]
        received_qty = qty_ordered if not partial else max(1, int(qty_ordered * rng.uniform(0.4, 0.8)))
        receipts = [
            {
                "received_at": ordered_at + timedelta(days=rng.randint(5, 20)),
                "lines": [{"po_line_no": 1, "qty": received_qty, "qc_result": "pass"}],
            }
        ]
        if "qty_over" in diff_types:  # 到货不足：只对 qty_over 样本注入，避免产生"未被 gold 建模"的差异
            received_qty = max(0, received_qty - rng.choice((1, 2)))
            receipts[0]["lines"][0]["qty"] = received_qty

        # ---- 合同（10% 无合同；15% 扫描件）；terms_conflict 样本必须有合同才可判定
        has_contract = "terms_conflict" in diff_types or rng.random() >= cfg.no_contract_ratio
        contract = None
        if has_contract:
            contract = {
                "contract_no": contract_no,
                "type": "one_off",
                "total_amount": total,
                "currency": "CNY",
                "payment_terms": {"days": 30, "mode": "验收后电汇", "milestones": []},
                "delivery_terms": {"lead_time_days": (promised - ordered_at.date()).days, "penalty_clause": "逾期每日 0.5%"},
                "valid_from": ordered_at.date(),
                "valid_to": promised + timedelta(days=180),
                "lines": [
                    {
                        "line_no": 1,
                        "item_desc": sku["name"],
                        "spec_bucket": sku["spec_bucket"],
                        "unit_price_tax_excl": unit_price,
                        "qty": qty_ordered,
                        "amount_tax_excl": amount,
                        "tax_rate": TAX_RATE,
                    }
                ],
            }

        # ---- 发票（可能多张；异常在这里注入）
        invoice_qty = received_qty if partial else qty_ordered
        inv_unit_price = unit_price
        inv_tax_rate = TAX_RATE
        tail_offset = 0.0
        if "price" in diff_types:
            inv_unit_price = money(unit_price * (1 + rng.uniform(0.03, 0.20)))
            anomalies.append(
                {
                    "type": "price",
                    "po_line_no": 1,
                    "invoice_line_no": 1,
                    "expected": unit_price,
                    "actual": inv_unit_price,
                    "delta": money(inv_unit_price - unit_price),
                    "rule_ref": "TOL-PRICE-01",
                    "severity": "medium",
                }
            )
        if "tax_rate" in diff_types:
            inv_tax_rate = 0.09
            anomalies.append(
                {
                    "type": "tax_rate",
                    "po_line_no": 1,
                    "invoice_line_no": 1,
                    "expected": TAX_RATE,
                    "actual": inv_tax_rate,
                    "delta": money(inv_tax_rate - TAX_RATE),
                    "rule_ref": "TOL-TAX-01",
                    "severity": "high",
                }
            )
        if "qty_over" in diff_types:
            over_qty = rng.choice((1, 2, 3))
            invoice_qty = max(qty_ordered, received_qty) + over_qty
            anomalies.append(
                {
                    "type": "qty_over",
                    "po_line_no": 1,
                    "invoice_line_no": 1,
                    "expected": float(max(qty_ordered, received_qty)),
                    "actual": float(invoice_qty),
                    "delta": float(invoice_qty - max(qty_ordered, received_qty)),
                    "rule_ref": "TOL-QTY-01",
                    "severity": "high",
                }
            )
        inv_amount, inv_tax, inv_total = _line(invoice_qty, inv_unit_price, inv_tax_rate)
        line_tax = money(inv_amount * inv_tax_rate)
        line_total_exact = money(inv_amount + line_tax)
        if "tail" in diff_types:
            tail_offset = rng.choice((0.02, 0.03, -0.02, -0.03))
            inv_total = money(inv_total + tail_offset)
            inv_tax = money(inv_total - inv_amount)
            anomalies.append(
                {
                    "type": "tail",
                    "po_line_no": 1,
                    "invoice_line_no": 1,
                    "expected": money(inv_amount + money(inv_amount * inv_tax_rate)),
                    "actual": inv_total,
                    "delta": tail_offset,
                    "rule_ref": "TOL-TAIL-01",
                    "severity": "low",
                }
            )
        if "terms_conflict" in diff_types:
            anomalies.append(
                {
                    "type": "terms_conflict",
                    "po_line_no": 1,
                    "invoice_line_no": 1,
                    "expected": None,
                    "actual": None,
                    "delta": None,
                    "rule_ref": "TOL-TERMS-01",
                    "severity": "medium",
                    "detail": "合同约定验收后 30 天付款，发票备注为预付 50%",
                }
            )
        if "date_overdue" in diff_types:
            overdue_days = rng.randint(8, 25)
            actual_receipt = promised + timedelta(days=overdue_days)
            last_day = BASE_DATE - timedelta(days=2)
            if actual_receipt > last_day:
                # 收货日不能落在未来：整体前移，同时保持"逾期 overdue_days 天"的语义
                actual_receipt = last_day
                promised = actual_receipt - timedelta(days=overdue_days)
            receipts[0]["received_at"] = datetime.combine(actual_receipt, datetime.min.time()) + timedelta(hours=10)
            anomalies.append(
                {
                    "type": "date_overdue",
                    "po_line_no": 1,
                    "invoice_line_no": None,
                    "expected": promised.isoformat(),
                    "actual": actual_receipt.isoformat(),
                    "delta": float((actual_receipt - promised).days),
                    "rule_ref": "TOL-DATE-01",
                    "severity": "low",
                }
            )

        # 普通单据的收货日期兜底：不应晚于"今天"（BASE_DATE），否则发票会落在未来 → DATE_FUTURE
        last_day = BASE_DATE - timedelta(days=2)
        if receipts[0]["received_at"].date() > last_day:
            receipts[0]["received_at"] = datetime.combine(last_day, datetime.min.time()) + timedelta(hours=10)

        scanned = i in scanned_group_ids
        invoice_no = f"INV-{FISCAL_YEAR}-{3000 + i:04d}"
        invoice_date = (receipts[0]["received_at"] + timedelta(days=rng.randint(1, 10))).date()
        invoice_date = min(invoice_date, BASE_DATE - timedelta(days=1))
        invoices = [
            {
                "invoice_no": invoice_no,
                "invoice_code": f"011002{2026}{i:04d}",
                "invoice_date": invoice_date,
                "amount_tax_excl": inv_amount,
                "tax_amount": inv_tax,
                "amount_tax_incl": inv_total,
                "lines": [
                    {
                        "line_no": 1,
                        "item_desc": sku["name"],
                        "spec_bucket": sku["spec_bucket"],
                        "qty": float(invoice_qty),
                        "unit_price_tax_excl": inv_unit_price,
                        "tax_rate": inv_tax_rate,
                        "amount_tax_excl": inv_amount,
                        "amount_tax_incl": line_total_exact,
                    }
                ],
                "remark": "预付 50%" if "terms_conflict" in diff_types else "",
            }
        ]

        hard = {"price", "tax_rate", "qty_over", "terms_conflict"} & set(diff_types)
        if hard:
            overall = "hold"
        elif {"tail", "date_overdue"} & set(diff_types):
            overall = "release_with_note"
        else:
            overall = "pass"

        groups.append(
            {
                "case_id": case_id,
                "category_code": category,
                "supplier_code": supplier["code"],
                "sku_code": sku["sku_code"],
                "po": {
                    "po_no": po_no,
                    "ordered_at": ordered_at,
                    "status": "partial" if partial else "received",
                    "currency": "CNY",
                    "total_amount_tax_incl": total,
                    "lines": [
                        {
                            "line_no": 1,
                            "item_desc": sku["name"],
                            "spec_bucket": sku["spec_bucket"],
                            "qty_ordered": float(qty_ordered),
                            "unit_price_tax_excl": unit_price,
                            "tax_rate": TAX_RATE,
                            "amount_tax_excl": amount,
                            "amount_tax_incl": total,
                            "promised_date": promised,
                        }
                    ],
                },
                "receipts": receipts,
                "contract": contract,
                "invoices": invoices,
                "diff_types": sorted(set(diff_types)),
                "anomalies": anomalies,
                "scanned": scanned,
                "partial_invoice": partial,
                "requires_gr": requires_gr[category],
                "expected_overall": overall,
            }
        )
        gold.append(
            {
                "case_id": case_id,
                "po_no": po_no,
                "contract_no": contract_no if contract else None,
                "category_code": category,
                "requires_gr": requires_gr[category],
                "expected_overall": overall,
                "expected_diffs": anomalies,
                "expected_line_matches": [
                    {"po_line_no": 1, "invoice_line_no": 1, "method": "L1_exact"}
                ],
                "expected_partial": partial,
                "scanned": scanned,
            }
        )

    return groups, gold


def build_dataset(cfg: SeedConfig | None = None) -> dict[str, Any]:
    """构建全量数据集（确定性：同一 seed 输出完全一致）。"""
    cfg = cfg or SeedConfig()
    rng = make_rng(cfg.seed)
    master = _build_master(rng, cfg)
    prs, pr_gold = _build_prs(rng, cfg, master)
    groups, m3_gold = _build_doc_groups(rng, cfg, master)

    diff_counter: dict[str, int] = {}
    for case in m3_gold:
        for diff in case["expected_diffs"]:
            diff_counter[diff["type"]] = diff_counter.get(diff["type"], 0) + 1

    overall_counter: dict[str, int] = {}
    for case in m3_gold:
        overall_counter[case["expected_overall"]] = overall_counter.get(case["expected_overall"], 0) + 1

    return {
        "meta": {
            "seed": cfg.seed,
            "generated_at": "2026-10-23",
            "counts": {
                "departments": len(master["departments"]),
                "users": len(master["users"]),
                "budgets": len(master["budgets"]),
                "categories": len(master["categories"]),
                "rules": len(master["rules"]),
                "suppliers": len(master["suppliers"]),
                "certs": len(master["certs"]),
                "skus": len(master["skus"]),
                "policies": len(master["policies"]),
                "po_history": len(master["po_history"]),
                "inventory": len(master["inventory"]),
                "tickets": len(master["tickets"]),
                "prs": len(prs),
                "doc_groups": len(groups),
            },
            "m3_diff_coverage": diff_counter,
            "m3_overall_distribution": overall_counter,
            "m1_verdict_distribution": {
                verdict: sum(1 for g in pr_gold if g["expected_verdict"] == verdict)
                for verdict in ("pass", "needs_info", "block")
            },
        },
        "master": master,
        "prs": prs,
        "pr_gold": pr_gold,
        "doc_groups": groups,
        "m3_gold": m3_gold,
    }

