"""M1 规则 DSL：JSONLogic 白名单求值器 + 规则执行（Spec §5.2 / §5.4）。

设计要点：
- **表达式即数据**：规则存 `rule.expression`（JSONB），代码只解释白名单算子（不 eval 字符串）；
- 自定义算子：`required`（必填校验）、`fn`（上下文函数，如 `budget_available`）；
- 表达式为真 = 命中规则（required 算子语义为"存在缺失即命中"）；
- 每条 finding 带 rule_code 与 policy 证据引用，供前端证据面板。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

ALLOWED_COMPARISONS = {"==", "!=", ">", ">=", "<", "<="}
ARITHMETIC = {"+", "-", "*", "/", "%"}
ALLOWED_OPERATORS = ALLOWED_COMPARISONS | ARITHMETIC | {
    "var",
    "required",
    "fn",
    "in",
    "and",
    "or",
    "not",
    "if",
    "cat",
    "len",
    "exists",
}


class RuleEvalError(ValueError):
    """规则非法或求值失败：宁可显式报错，也不静默放过。"""


@dataclass(frozen=True, slots=True)
class RuleDef:
    code: str
    type: str
    severity: str
    field_path: str | None
    expression: dict[str, Any]
    message: str
    policy_ref: str | None = None
    category_code: str | None = None
    version: int = 1


def parse_path(path: str) -> list[str | int]:
    tokens: list[str | int] = []
    for part in path.replace("/", ".").split("."):
        if not part:
            continue
        match = re.match(r"^([A-Za-z_]\w*)((?:\[\d+\])*)$", part)
        if match:
            tokens.append(match.group(1))
            tokens.extend(int(index) for index in re.findall(r"\[(\d+)\]", match.group(2)))
        else:
            tokens.append(part)
    return tokens


def get_path(data: Any, path: str, default: Any = None) -> Any:
    current = data
    for token in parse_path(path):
        try:
            if isinstance(token, int):
                if not isinstance(current, (list, tuple)) or token >= len(current):
                    return default
                current = current[token]
            else:
                if not isinstance(current, dict) or token not in current:
                    return default
                current = current[token]
        except (TypeError, KeyError):
            return default
    return current


def is_missing(value: Any) -> bool:
    return value is None or value == "" or (isinstance(value, (list, dict)) and not value)


def normalize_field(field_path: str | None) -> str:
    """规则字段路径 → gold 一致的斜杠格式：items[*].spec.memory_gb → /items/0/spec/memory_gb。"""
    if not field_path:
        return ""
    path = re.sub(r"\[(\d+|\*)\]", r"/\1", field_path).replace(".", "/")
    return "/" + path.lstrip("/").replace("*", "0")


def _first_missing(expression: dict[str, Any], data: dict[str, Any]) -> str | None:
    if "required" not in expression:
        return None
    paths = expression["required"]
    if isinstance(paths, str):
        paths = [paths]
    for path in paths:
        if is_missing(get_path(data, str(path))):
            return str(path)
    return None


def evaluate(expression: Any, data: dict[str, Any]) -> Any:
    """白名单 JSONLogic 求值；未声明算子直接抛错（防止规则绕过）。"""
    if not isinstance(expression, dict):
        return expression
    if len(expression) != 1:
        raise RuleEvalError(f"表达式必须只有一个算子键：{expression!r}")
    operator, args = next(iter(expression.items()))
    if operator not in ALLOWED_OPERATORS:
        raise RuleEvalError(f"算子不在白名单：{operator}")

    if operator == "var":
        return get_path(data, str(args))
    if operator == "required":
        paths = args if isinstance(args, list) else [args]
        return any(is_missing(get_path(data, str(path))) for path in paths)
    if operator == "exists":
        return not is_missing(get_path(data, str(args)))
    if operator == "fn":
        name = str(args)
        if name not in data:
            raise RuleEvalError(f"未知上下文函数：{name}")
        return data[name]
    if operator == "in":
        needle = evaluate(args[0], data)
        haystack = evaluate(args[1], data) or []
        return needle in haystack
    if operator in ALLOWED_COMPARISONS:
        left, right = evaluate(args[0], data), evaluate(args[1], data)
        try:
            # 注意：不能用 dict 字面量一次性求值，否则 None > True 会先抛 TypeError
            if operator == "==":
                return left == right
            if operator == "!=":
                return left != right
            if operator == ">":
                return left > right
            if operator == ">=":
                return left >= right
            if operator == "<":
                return left < right
            return left <= right
        except TypeError as exc:
            raise RuleEvalError(f"比较类型不匹配：{left!r} {operator} {right!r}") from exc
    if operator in ARITHMETIC:
        left, right = evaluate(args[0], data), evaluate(args[1], data)
        try:
            if operator == "+":
                return left + right
            if operator == "-":
                return left - right
            if operator == "*":
                return left * right
            if operator == "/":
                return left / right
            return left % right
        except (TypeError, ZeroDivisionError) as exc:
            raise RuleEvalError(f"算术运算失败：{left!r} {operator} {right!r}") from exc
    if operator in {"and", "or"}:
        values = [bool(evaluate(item, data)) for item in args]
        return all(values) if operator == "and" else any(values)
    if operator == "not":
        inner = args[0] if isinstance(args, list) else args
        return not bool(evaluate(inner, data))
    if operator == "if":
        if bool(evaluate(args[0], data)):
            return evaluate(args[1], data)
        return evaluate(args[2], data) if len(args) > 2 else None
    if operator == "cat":
        return "".join(str(evaluate(item, data)) for item in args)
    if operator == "len":
        value = evaluate(args[0], data)
        try:
            return len(value)
        except TypeError as exc:
            raise RuleEvalError(f"len 作用在不可测长对象：{type(value).__name__}") from exc
    raise RuleEvalError(f"未实现的算子：{operator}")  # pragma: no cover - 白名单已覆盖


def evaluate_rules(rules: list[RuleDef], context: dict[str, Any]) -> list[dict[str, Any]]:
    """执行规则集，返回 finding 列表（表达式为真 = 命中）。"""
    findings: list[dict[str, Any]] = []
    for rule in rules:
        try:
            triggered = bool(evaluate(rule.expression, context))
        except RuleEvalError as exc:
            raise RuleEvalError(f"规则 {rule.code} 求值失败：{exc}") from exc
        if not triggered:
            continue
        field = normalize_field(rule.field_path)
        missing_path = _first_missing(rule.expression, context)
        if missing_path:
            field = normalize_field(missing_path)
        finding: dict[str, Any] = {
            "rule_code": rule.code,
            "type": rule.type,
            "severity": rule.severity,
            "field": field or None,
            "message": rule.message,
        }
        if rule.policy_ref:
            finding["evidence"] = [{"kind": "policy", "ref": rule.policy_ref}]
        findings.append(finding)
    return findings


def verdict_from_findings(findings: list[dict[str, Any]]) -> str:
    """block 只来自硬政策违规（policy_ref）；普通 high 问题仍需补信息。"""
    if any(finding["type"] == "policy_ref" for finding in findings):
        return "block"
    return "needs_info" if findings else "pass"

