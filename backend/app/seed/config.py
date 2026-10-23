"""数据工厂配置与确定性随机源（Spec §10.1/§10.2）。"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = BACKEND_ROOT.parent


@dataclass(slots=True)
class SeedConfig:
    seed: int = 42
    # 主数据规模（Spec §10.2）
    department_count: int = 6
    supplier_count: int = 30
    sku_count: int = 100
    policy_count: int = 60
    po_history_count: int = 300
    service_ticket_count: int = 120
    inventory_count: int = 20
    # 单据规模
    pr_count: int = 120
    doc_groups: int = 80
    # 异常比例
    scanned_ratio: float = 0.15
    partial_invoice_ratio: float = 0.30
    anomaly_ratio: float = 0.45
    no_contract_ratio: float = 0.10
    # 输出目录
    data_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "data")
    clean: bool = True

    @property
    def files_dir(self) -> Path:
        return self.data_dir / "files"

    @property
    def gold_dir(self) -> Path:
        return self.data_dir / "gold"


CATEGORIES: tuple[tuple[str, str, bool], ...] = (
    # (category_code, 中文名, requires_gr)
    ("IT_HW", "IT硬件", True),
    ("SAAS", "SaaS订阅", False),
    ("ADMIN", "行政办公", True),
    ("MKT_OUTSOURCE", "市场活动与外包", False),
)

CATEGORY_NAMES = {code: name for code, name, _ in CATEGORIES}

# 重点覆盖 M3 的差异类型（每类至少 10 个正样本，Spec §10.3）
DIFF_PLAN: tuple[tuple[str, int], ...] = (
    ("price", 12),
    ("tax_rate", 8),
    ("qty_over", 12),
    ("date_overdue", 10),
    ("terms_conflict", 8),
    ("tail", 10),
)

SUPPLIER_NAMES: tuple[str, ...] = (
    "北京华信科技有限公司",
    "上海联启信息技术有限公司",
    "深圳迈凯电子有限公司",
    "杭州云枢软件有限公司",
    "广州锐捷办公设备有限公司",
    "南京诚盟系统集成有限公司",
    "成都天府智联科技有限公司",
    "武汉长江数据服务有限公司",
    "苏州工业园区恒创电子有限公司",
    "北京中科智采供应链有限公司",
    "上海昱诚商务服务有限公司",
    "深圳市南山区睿博科技有限公司",
    "杭州西湖文创策划有限公司",
    "北京朝阳会务服务有限公司",
    "上海浦东广告传媒有限公司",
    "广州天河人力外包有限公司",
    "西安高新信息技术有限公司",
    "合肥科大讯飞系软件有限公司",
    "天津港保税区物资有限公司",
    "重庆两江新区智能制造有限公司",
    "长沙麓谷云计算有限公司",
    "青岛海诺办公用品有限公司",
    "厦门软件园数字科技有限公司",
    "宁波鄞州办公家具有限公司",
    "无锡新吴区精密仪器有限公司",
    "佛山顺德家电服务有限公司",
    "东莞松山湖电子制造有限公司",
    "珠海横琴数据科技有限公司",
    "福州鼓楼信息安全有限公司",
    "济南历下区咨询管理有限公司",
)

DEPARTMENTS: tuple[tuple[str, str], ...] = (
    ("信息技术部", "CC-IT-001"),
    ("行政部", "CC-ADM-002"),
    ("市场部", "CC-MKT-003"),
    ("财务部", "CC-FIN-004"),
    ("人力资源部", "CC-HR-005"),
    ("研发中心", "CC-RD-006"),
)


def make_rng(seed: int) -> random.Random:
    return random.Random(seed)


def money(value: float) -> float:
    return round(value + 1e-9, 2)
