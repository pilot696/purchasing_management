# 评测报告（合成数据基线）

> 生成日期：2026-10-23 ｜ seed=42 ｜ 运行模式：**离线 FakeLLM**（`FORCE_FAKE_LLM=1`，真实 API 未接入）
> 原始数据：[`evals/extraction_baseline.json`](evals/extraction_baseline.json)、[`evals/m1_baseline.json`](evals/m1_baseline.json)、
> [`evals/m2_baseline.json`](evals/m2_baseline.json)、[`evals/m3_baseline.json`](evals/m3_baseline.json)、汇总 [`evals/latest.json`](evals/latest.json)

## 0. 一句话结论

三条 Agent 链路全部端到端可跑、可回放、可审计：M3 三单匹配 80 组、M1 预检 120 条、M2 供应商 30 家；
确定性管线（抽取路由/对齐/容差/规则/评分/闸门）在**与数据工厂同源的金标**上达到 100% 回归一致性，
检索在真实 DB 数据上 top-1 = 100%（105 样本）；**这些数字证明"实现与契约一致"，不代表真实业务精度**。

## 1. 数据集（全部合成，seed=42）

| 语料 | 规模 | 说明 |
|---|---|---|
| 采购申请 PR | 120 条（6 类异常 × 20） | 缺字段/预算超限/政策违规/数量异常/歧义/干净 |
| 三单匹配 | 80 组（237 份文档） | 合同 77 / 发票 80 / PO 80；扫描件 24 份；分批开票 32 组；`requires_gr` 72/8 |
| 供应商 | 30 家 | 6 家注入 6 类闸门异常、3 家黑名单、4 家证照临期、8 家冷启动 |
| 规则/权重/政策 | 17 条规则、4 品类权重、60 条政策条款 | 品类模板驱动 |

> 数据工厂"生成即金标"：PDF 由 ReportLab 生成，异常由构造器注入。因此金标与实现同源，
> 100% 是**回归基线**；真实精度必须在真实票据/真实供应商数据上评测（见 §7）。

## 2. 抽取基线（M3 文档管线）

| 指标 | 数值 | 样本 |
|---|---|---|
| 路由分布 | pdf_native 213 / scanned 24 | 237 |
| 发票关键字段准确率 | invoice_no 100% / amount_tax_incl 100% / seller_name 100% | 68 张数字发票 |
| 数字 PDF 抽取延迟 | p50 39.15ms / p95 46.14ms | 213 |
| 抽取失败 | 24（全部为扫描件，离线未跑 VLM） | 237 |

**边界**：扫描件路径走 VLM + Schema + repair loop 的逻辑已实现并有单测（`test_vlm_extraction.py`），
但离线 FakeLLM 不产出真实视觉结果，因此扫描件准确率与单票成本**未测**，不计入精度结论。

## 3. M1 预检（120 条 PR）

| 指标 | 数值 | 说明 |
|---|---|---|
| verdict 一致率 | **100%**（120/120） | 6 类异常各 20 条 |
| findings 精确匹配 | **100%**（120/120） | micro P/R/F1 = 1.0（tp=112） |
| 检索 top-1 等价类准确率 | **100%**（105 样本） | 真实 DB 数据；无歧义样本 |
| 补丁 | 60/120 用例产生补丁（共 120 条） | 缺字段/歧义场景 |
| 链路耗时 | 1567ms / 120 条 | 确定性链路，不调 LLM |

**方法**：规则 DS L（JSONLogic 白名单）+ 三路检索（pg_trgm 词法 / 精确名与别名 / 规格桶结构化）+ RRF；
verdict 由规则引擎算出，LLM schema 中无 verdict 字段。**未做**：向量 leg（embedding provider 未接入，ADR-020）、
dense vs hybrid 消融、recall@5（当前只评 top-1）。

## 4. M2 供应商评估（30 家）

| 指标 | 数值 | 说明 |
|---|---|---|
| 闸门检出率 | **100%**（9/9） | 6 类注入异常 + 3 家黑名单 |
| 闸门误报 | **0%**（0/30） | 无 veto 预期却出现否决级闸门 |
| 冷启动一致率 | **100%**（12/12） | 强制 `conditional` + `low` 置信度 |
| 结论分布 | approve 2 / conditional 21 / reject 3 / frozen 4 | 9 家闸门短路无评分 |
| 权重敏感性 | 平均 flip_rate **5.55%** | 300 次 U(0.8,1.2) 扰动，seed=42 |
| 维度覆盖 | 非否决供应商 5/5 维 | 闸门短路者不评分 |
| 耗时 | 312ms / 30 家 | 确定性评分，不调 LLM |

**方法**：6 条硬闸门（G2 经营范围/G6 安全资质为条件项不否决）→ 五维公式评分 → 300 次权重扰动 →
LLM 复核者叙述（schema 无分数，`evidence_refs` 后置校验）。**未测**：维度分 MAE、结论 Cohen's κ、
风险点 recall（需专家标注 gold 或 LLM-as-judge 协议）；离线 LLM 复核者全部标记 `unavailable`（不伪造正文）。

## 5. M3 三单匹配（80 组）

| 指标 | 数值 | 说明 |
|---|---|---|
| 行对齐 F1 | **1.0**（80/80） | 匈牙利指派 |
| 差异检出 micro F1 | **1.0**（tp=60） | 6 类 diff：price 12 / qty_over 12 / tail 10 / date_overdue 10 / tax_rate 8 / terms_conflict 8 |
| 差异误报 | **0**（fp=0） | — |
| 算法耗时 | 2.1ms / 组 | 不含 PDF 抽取与 HTTP |
| 贪心基线 | 同上 1.0 | 每组仅 1 个 PO 行，指标相同；匈牙利优势由冲突矩阵单测证明 |

**方法**：三级对齐（精确键 / `spec_bucket` 归一 / 语义）+ 匈牙利全局指派；五类容差表 + 尾差池 +
`ordered/received/invoiced/remaining` 分批核销状态机；HITL 前不写账（`qty_invoiced`、发票状态、尾差池只在 finalize 落库）。
**未测**：端到端 P95（含 PDF 抽取/SSE/前端）。

## 6. 工程验证

| 项 | 结果 |
|---|---|
| 后端单测 | **90 passed / 3 skipped**（93 个） |
| 后端集成测试（DB） | **24 passed / 4 skipped**（28 个） |
| 前端单测 | **45 passed** |
| 前端构建 | **390.71 KB / gzip 117.20 KB** |
| 真实 e2e 冒烟 | `SMOKE_M1=OK` / `SMOKE_M2=OK` / `SMOKE_M3=OK` / `SMOKE_E2E=OK`；M3 双发票 HTTP e2e 通过（两张 invoice.status=matched） |
| 容器 | `docker compose up -d` 一键起（API 托管前端 dist，`/healthz` 探活） |

### 6.1 M2 真实模型全量基线（2026-10-24）

命令：`python scripts/m2_eval.py --real-llm --clear-cache`（deepseek-v4.1-flash，失败自动降级 glm-5.3-flash）。

| 项 | 结果 |
|---|---|
| 供应商 | 30 |
| LLM 状态 | **23 ok / 7 skipped_gate_hit / 0 failed**（修复 max_tokens 截断后重跑冷缓存） |
| 耗时 | **522s（8.7min，23 次真实调用）** |
| tokens（缓存 usage 汇总） | **35,386 in / 82,960 out** |
| 估算成本 | **¥0.391**（DeepSeek 0.15/0.60 USD per 1M、fx=7.1；不含失败/重试，网关账单为准） |
| 闸门检出 / 误报 | **100% / 0%**（9 个注入异常全命中） |
| 结论分布 | approve 2 / conditional 21 / frozen 4 / reject 3 |
| 冷启动 | 12/12 强制 conditional + low |
| 敏感性 flip_rate | 5.99%（300×U(0.8,1.2)） |
| 维度完整性 | 3.83/5（部分供应商缺 OTD/QC 数据） |

> 口径说明：`total_score.mean=60.95` 含闸门否决的 0 分，不能当"平均质量分"；`llm_failures=[]` 但 23 家是 LLM 参与、
> 7 家被闸门短路；M2 仍**没有专家 gold**，23 家真实复核只能证明"可调用、可解析、可引用校验、无整体降级"，
> 不能证明风险判断准确率。

### 6.2 扫描件 VLM 真实基线（2026-10-24）

命令：`python scripts/extract_eval.py --vision --scanned-only --clear-cache`
（百炼 OpenAI 兼容端点，`qwen3.8-max`，24 份 seed 扫描件：12 合同 + 12 发票）。

| 项 | 结果 |
|---|---|
| 文档数 | 24（全部路由 scanned） |
| 抽取成功 | **24/24**（12 合同 + 12 发票；ok=24，needs_review=0，failed=0） |
| 发票字段准确率 | 发票号 / 价税合计 / 销方名称 **100%**（n=12） |
| 延迟 | p50 **35.7s** / p95 **49.3s** / mean 35.6s |
| 调用数 / tokens | 31 次（含 repair） / **94,107 in + 36,486 out** |
| 估算成本 | **¥2.89**（qwen3.8-max 2.0/6.0 USD per 1M、fx=7.1；百炼账单为准），约 **¥0.12/份** |

> 口径说明：这是**合成扫描件**上的"可抽取率 + 12 张发票 3 字段准确率"，不是真实票据版式精度；合同/PO 只统计了
> 成功抽取与 high-severity 校验问题（0 个），**没有逐字段 gold**；VLM 输出没有 bbox，扫描件 PDF 抽屉暂无高亮
> （原生 PDF 才有 bbox）。另外 VLM 常把税率 13% 写成 13，已在抽取层归一化为 0.13。

## 7. 诚实边界（简历/面试必须主动说明）

1. **合成数据**：所有单据、供应商、金标均为自建工厂生成，不代表真实企业分布。
2. **金标同源**：M3 的 diff gold 与 reconcile 规则同源、M1 的 gold 与规则引擎同源——1.0 只能证明"实现未回归"。
3. **离线/真实口径分开**：确定性基线（M1/M3/native 抽取）用 `FORCE_FAKE_LLM=1` 跑；M2（§6.1）与扫描件 VLM（§6.2）
   已有真实全量基线，但仍是"可调用/可解析/可降级"的工程验证，不是专家质量评估；真实模型全量重跑需费用与网关可用性。
4. **检索未接向量**：当前 RRF 由词法 + 精确 + 结构化三路组成；embedding provider 接入后再补 dense leg 与消融。
5. **M2 缺专家 gold**：目前验证的是闸门/冷启动/敏感性契约；维度分 MAE 与 κ 需人工标注。
6. **生产限制**：LangGraph `MemorySaver` 为进程内 checkpointer，多副本需换 Postgres saver；
   M3 已支持同一 PO 多发票（≤20 张，顺序核销；e2e 已验证双发票 run）。
7. **人工基线**："人工 8min/组"是面板估算（estimated），非实测计时。

## 8. 复现命令

```bash
# 1) 起库 + 迁移 + 种子
docker compose up -d db
cd backend && alembic upgrade head
python scripts/seed.py --seed 42

# 2) 三套基线（确定性，离线）
python scripts/extract_eval.py
python scripts/m1_eval.py
python scripts/m2_eval.py
python scripts/m3_eval.py

# 2.1) M2 真实模型全量（产生费用，清缓存；写 evals/m2_baseline_real.json）
python scripts/m2_eval.py --real-llm --clear-cache

# 2.2) 扫描件 VLM（需 OpenAI 兼容视觉端点；先小样本）
python scripts/extract_eval.py --vision --limit-docs 5 --clear-cache

# 3) 单元 / 集成 / 前端
pytest -m "not integration"
pytest -m integration
cd frontend && pnpm run test && pnpm run build

# 4) 真实 e2e（另起终端）
cd backend && python run.py --port 8012
python scripts/smoke_m1.py && python scripts/smoke_m2.py && python scripts/smoke_m3.py
```

> 数字均来自上述命令的最近一次运行；`evals/latest.json` 为汇总快照，不手工编辑单项指标。
