import json
from collections import Counter, defaultdict

from app.matching.normalize import normalize
from app.rules.engine import RuleDef, evaluate_rules, verdict_from_findings
from app.seed.config import SeedConfig
from app.seed.dataset import build_dataset

ds = build_dataset(SeedConfig(seed=42))
budgets = {(b["department_cost_center"], b["category_code"]): b for b in ds["master"]["budgets"]}
rules_by_cat = defaultdict(list)
for rule in ds["master"]["rules"]:
    rules_by_cat[rule["category_code"]].append(
        RuleDef(
            code=rule["code"],
            type=rule["type"],
            severity=rule["severity"],
            field_path=rule["field_path"],
            expression=rule["expression"],
            message=rule["message_template"],
            policy_ref=rule.get("policy_clause_ref"),
            category_code=rule["category_code"],
        )
    )

prs = {pr["pr_no"]: pr for pr in ds["prs"]}
totals = Counter()
mismatch = []
for gold in ds["pr_gold"]:
    pr = prs[gold["pr_no"]]
    item = pr["items"][0]
    budget = budgets[(pr["department_cost_center"], pr["category_code"])]
    available = budget["amount_total"] - budget["amount_used"] - budget["amount_committed"]
    context = {
        "items": [{**item, "owner": item["spec"].get("owner"), "cost_center": item["spec"].get("cost_center")}],
        "estimated_amount": pr["estimated_amount"],
        "budget_available": available,
    }
    try:
        findings = evaluate_rules(rules_by_cat[pr["category_code"]], context)
    except Exception as exc:
        print("RULE_ERROR", gold["pr_no"], exc)
        raise
    norm = normalize(item["item_name_raw"], category_code=pr["category_code"])
    meaningful = {k: v for k, v in item["spec"].items() if k not in ("owner", "cost_center")}
    if not meaningful and "*" in norm.spec_bucket:
        findings = [
            finding
            for finding in findings
            if not (finding["type"] == "required" and (finding["field"] or "").startswith("/items/0/"))
        ]
        findings.append(
            {"rule_code": "SEMANTIC-AMBIGUITY", "type": "ambiguity", "severity": "high", "field": "/items/0/item_name", "message": "品名过于笼统，缺少规格"}
        )
    verdict = verdict_from_findings(findings)
    expected = {(d["type"], d["field"], d["severity"]) for d in gold["expected_findings"]}
    got = {(d["type"], d["field"], d["severity"]) for d in findings}
    totals["cases"] += 1
    totals["verdict_ok" if verdict == gold["expected_verdict"] else "verdict_bad"] += 1
    if verdict == gold["expected_verdict"]:
        totals["verdict_ok"] += 0
    if got == expected:
        totals["findings_exact"] += 1
    else:
        mismatch.append(
            {
                "pr": gold["pr_no"],
                "anomaly": gold["anomaly"],
                "expected_verdict": gold["expected_verdict"],
                "got_verdict": verdict,
                "missing": sorted(expected - got),
                "extra": sorted(got - expected),
            }
        )
print(json.dumps({k: totals[k] for k in ("cases", "verdict_ok", "verdict_bad", "findings_exact")}, ensure_ascii=False))
for row in mismatch[:15]:
    print(json.dumps(row, ensure_ascii=False))
print("mismatch_count", len(mismatch))

