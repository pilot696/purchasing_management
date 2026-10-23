"""规则 DSL 单测：白名单求值、required 语义、字段路径规范化、verdict 推导。"""

from __future__ import annotations

import pytest

from app.rules.engine import (
    RuleDef,
    RuleEvalError,
    evaluate,
    evaluate_rules,
    normalize_field,
    verdict_from_findings,
)


def test_required_operator_semantics() -> None:
    context = {"items": [{"spec": {"storage_gb": 512}}]}
    assert evaluate({"required": ["items[0].spec.memory_gb"]}, context) is True
    assert evaluate({"required": ["items[0].spec.storage_gb"]}, context) is False


def test_comparisons_do_not_crash_on_type_mix() -> None:
    # None == True 必须返回 False，而不是在比较 None > True 时抛 TypeError
    context = {"items": [{"spec": {}}]}
    assert evaluate({"==": [{"var": "items[0].spec.related_party"}, True]}, context) is False
    with pytest.raises(RuleEvalError):
        evaluate({">": [{"var": "items[0].spec.memory_gb"}, 8]}, context)


def test_in_and_not_operators() -> None:
    context = {"items": [{"unit": "把"}]}
    assert evaluate({"not": {"in": [{"var": "items[0].unit"}, ["个", "台", "套"]]}}, context) is True
    assert evaluate({"not": {"in": ["台", ["个", "台", "套"]]}}, context) is False


def test_fn_context_and_unknown_fn() -> None:
    context = {"estimated_amount": 500, "budget_available": 100}
    assert evaluate({">": [{"var": "estimated_amount"}, {"fn": "budget_available"}]}, context) is True
    with pytest.raises(RuleEvalError):
        evaluate({"fn": "not_a_function"}, context)


def test_operator_whitelist_rejects_unknown() -> None:
    with pytest.raises(RuleEvalError):
        evaluate({"eval": "os.system('x')"}, {})
    with pytest.raises(RuleEvalError):
        evaluate({">": 1, "<": 2}, {})


def test_normalize_field_matches_gold_format() -> None:
    assert normalize_field("items[*].spec.memory_gb") == "/items/0/spec/memory_gb"
    assert normalize_field("items[0].qty") == "/items/0/qty"
    assert normalize_field("estimated_amount") == "/estimated_amount"


def _rule(code: str, expression: dict, **kwargs) -> RuleDef:
    payload = {
        "code": code,
        "type": "cross_field",
        "severity": "medium",
        "field_path": "estimated_amount",
        "expression": expression,
        "message": code,
    }
    payload.update(kwargs)
    return RuleDef(**payload)


def test_evaluate_rules_findings_shape() -> None:
    rules = [
        _rule("BUDGET", {">": [{"var": "estimated_amount"}, {"fn": "budget_available"}]}),
        _rule(
            "MISSING",
            {"required": ["items[0].spec.memory_gb"]},
            type="required",
            severity="high",
            field_path="items[*].spec.memory_gb",
            policy_ref="POL-1#1",
        ),
    ]
    context = {"items": [{"spec": {}}], "estimated_amount": 500, "budget_available": 100}
    findings = evaluate_rules(rules, context)
    assert [finding["rule_code"] for finding in findings] == ["BUDGET", "MISSING"]
    assert findings[1]["field"] == "/items/0/spec/memory_gb"
    assert findings[1]["evidence"] == [{"kind": "policy", "ref": "POL-1#1"}]


def test_rule_eval_failure_is_not_silent() -> None:
    with pytest.raises(RuleEvalError):
        evaluate_rules([_rule("BAD", {">": [{"var": "x"}, 1]})], {"x": None})


def test_verdict_mapping() -> None:
    assert verdict_from_findings([]) == "pass"
    assert verdict_from_findings([{"type": "required"}]) == "needs_info"
    assert verdict_from_findings([{"type": "cross_field"}, {"type": "policy_ref"}]) == "block"
