# 真实票据字段标注指南（双人独立标注 + Cohen's κ）

> 目标：为抽取准确率与 M2/M3 业务校验提供**可复核的人工 gold**，而不是把模型输出或合成数据再包装成"真实准确率"。
>
> 本文只定义流程。仓库内没有任何真实票据的人工标注结果；在两位标注者完成并校验之前，不允许在任何报告里写"真实票据准确率"。

## 1. 适用范围

- 真实（或脱敏后的）发票、采购订单、合同 PDF，建议每个文档类型先做 **30 份** 起步；
- 字段集固定为 `backend/app/evaluation/annotation.py` 中的 `DOC_FIELDS`：
  - 发票：`invoice_no`、`invoice_date`、`seller_name`、`seller_tax_id`、`amount_tax_excl`、`tax_rate`、`amount_tax_incl`、`currency`
  - 订单：`po_no`、`po_date`、`supplier_name`、`amount_tax_incl`、`currency`
  - 合同：`contract_no`、`contract_date`、`supplier_name`、`amount_tax_incl`、`currency`、`lead_time_days`
- 不涉及行项目/明细行；行项目 gold 需要单独的逐行对齐标注，不在本流程范围内。

## 2. 数据安全

- 真实发票常含供应商税号、银行账号、联系人等信息：**原始 PDF、标注 JSONL、标注过程中的截图都不要提交到公开仓库**；
- `data/annotation/` 已在 `.gitignore` 中忽略；报告 JSON 也只保留统计量，但输出路径仍建议放 `data/annotation/`；
- 如果已经提交过真实数据，删除历史 commit 中的文件并轮换相关凭据，而不是只删工作区文件。

## 3. 生成空白模板

### 3.1 已通过上传接口入库的文档

先启动后端并上传真实 PDF（页面「单据与文档」或 `POST /documents/upload`），然后：

```powershell
.venv\Scripts\python.exe scripts/annotation_agreement.py template `
  --doc-type invoice `
  --out data/annotation/template_invoice.jsonl
```

- 默认只导出 `data/files/uploads/` 下的上传文档（seed 合成文档不会被当成真实数据）；
- `--limit 30` 控制样本量；`--include-seed` 只用于流程演练，导出的数据**不能**用于真实准确率；
- 模板中 `values` 的每个字段都是 `null`，这是占位，不是模型抽取结果。

### 3.2 外部文档（未入库）

准备 `manifest.jsonl`（一行一个文档，不要写标注值）：

```json
{"doc_id": "real-inv-001", "doc_type": "invoice", "source_path": "data/annotation/raw/real-inv-001.pdf", "page_count": 1}
{"doc_id": "real-inv-002", "doc_type": "invoice", "source_path": "data/annotation/raw/real-inv-002.pdf", "page_count": 1}
```

```powershell
.venv\Scripts\python.exe scripts/annotation_agreement.py template `
  --manifest data/annotation/manifest.jsonl --doc-type invoice `
  --out data/annotation/template_invoice.jsonl
```

## 4. 两位标注者独立填写

把模板复制为两份，分别交给两位标注者，**不要展示模型抽取结果、不要互相讨论**：

```powershell
Copy-Item data/annotation/template_invoice.jsonl data/annotation/alice.jsonl
Copy-Item data/annotation/template_invoice.jsonl data/annotation/bob.jsonl
```

每行格式：

```json
{"doc_id": "real-inv-001", "doc_type": "invoice", "annotator": "alice", "source_path": "data/annotation/raw/real-inv-001.pdf", "page_count": 1, "values": {"invoice_no": "12345678901234567890", "invoice_date": "2026-03-05", "seller_name": "某某科技有限公司", "seller_tax_id": "91310000XXXXXXXXXX", "amount_tax_excl": "1000.00", "tax_rate": "13%", "amount_tax_incl": "1130.00", "currency": "CNY"}, "notes": ""}
```

填写规则：

1. **每个字段都必须出现**；确实无法识别时填 `null`，不要留空、不要猜。
2. 金额可写 `¥1,234.50`，日期可写 `2026/3/5` 或 `2026年3月5日`，税率可写 `13%`；脚本会归一化后再比较。
3. `annotator` 必须是两个不同的人名/代号；两份文件不能由同一人拆成两半填写。
4. `notes` 可记录遮挡、模糊、跨页等异常，不参与 κ 计算。
5. 不得把模型输出粘贴进标注文件；如已看过模型结果，应在报告中披露并视为非独立标注。

## 5. 校验并计算一致性

```powershell
.venv\Scripts\python.exe scripts/annotation_agreement.py check `
  --a data/annotation/alice.jsonl `
  --b data/annotation/bob.jsonl `
  --out data/annotation/agreement_invoice.json `
  --markdown data/annotation/agreement_invoice.md
```

输出：

- 校验错误（缺字段、未知字段、重复 doc_id、非法金额/日期、标注者相同等）会让命令以非 0 退出；
- 字段级 `n`、一致数、一致率、Cohen's κ、`both_missing`、`one_missing`；
- 整体槽位级一致率/κ 与字段宏平均（macro）；
- 报告会明确写"一致性不等于模型准确率"。

κ 的粗略解释（Landis & Koch，仅作参考；小样本不要过度解读）：

| κ | 一致性 |
| --- | --- |
| < 0 | 低于随机 |
| 0–0.20 | 轻微 |
| 0.21–0.40 | 一般 |
| 0.41–0.60 | 中等 |
| 0.61–0.80 | 较强 |
| 0.81–1.00 | 很强 |

## 6. 分歧裁决与 gold 形成

1. 只看 `agreement < 1` 的字段，由第三位裁决者（或业务 owner）看原始 PDF 决定正确值；
2. 生成 `adjudicated.jsonl`（结构与标注文件相同，`annotator` 写 `adjudicated`），**不要直接改 alice/bob 的文件**；
3. 用裁决后的共识标签计算抽取准确率；报告里分别写：
   - 标注者间 κ（本脚本）；
   - 与共识标签的字段准确率（下一步评测脚本，尚未实现）；
4. 若某字段 κ 很低（如 < 0.4），先修字段定义/标注指南，再扩大样本，不要用"模型更准"解释标注分歧。

## 7. 当前边界（必须如实写进简历/文档）

- 仓库只有流程与统计工具，**没有真实票据 gold**；没有跑过真实 κ，不得编造数字；
- 本工具只算**标注者之间**的一致性，不能替代模型准确率；
- seed 合成文档 + 独立模板生成器仍然同源，只能证明抽取实现自洽，不能证明真实泛化；
- 真实票据评测涉及企业数据授权，作品集展示时应说明数据来源与脱敏方式。
