from __future__ import annotations

import ast
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping

from .units import UnitError, UnitRegistry

ELIGIBLE = 'eligible'
INELIGIBLE = 'ineligible'
UNKNOWN = 'unknown'
INVALID = 'invalid'


class ExpressionError(ValueError):
    pass


@dataclass(frozen=True)
class ClauseTrace:
    """Evaluation record of one top-level conjunction clause."""

    clause_id: str
    expression: str
    result: str
    reasons: tuple[str, ...]
    facts_used: tuple[str, ...]
    fact_values: Mapping[str, Any]
    units: Mapping[str, str]
    evaluated: bool


@dataclass(frozen=True)
class Evaluation:
    result: str
    expression: str
    version_label: str
    missing: tuple[str, ...]
    reasons: tuple[str, ...]
    facts_used: tuple[str, ...]
    clauses: tuple[ClauseTrace, ...]
    invalid: bool = False


class _UnknownPropagation(Exception):
    """Internal signal: an expression branch referenced an absent fact."""

    def __init__(self, name: str):
        super().__init__(name)
        self.name = name


class SafeEvaluator:
    """Evaluates policy expressions under strict three-valued logic.

    * a missing fact yields ``unknown`` for the whole expression, never false;
    * every numeric comparison is checked through the :class:`UnitRegistry`
      so values with different units cannot be compared directly;
    * only a whitelist of AST nodes is accepted, so expressions cannot reach
      builtins or arbitrary attribute access.
    """

    ALLOWED = (
        ast.Expression, ast.BoolOp, ast.UnaryOp, ast.Compare, ast.Name, ast.Load,
        ast.Constant, ast.And, ast.Or, ast.Not, ast.Eq, ast.NotEq, ast.Lt, ast.LtE,
        ast.Gt, ast.GtE, ast.In, ast.NotIn, ast.List, ast.Tuple,
    )
    COMPARISONS = {
        ast.Eq: lambda a, b: a == b,
        ast.NotEq: lambda a, b: a != b,
        ast.Lt: lambda a, b: a < b,
        ast.LtE: lambda a, b: a <= b,
        ast.Gt: lambda a, b: a > b,
        ast.GtE: lambda a, b: a >= b,
        ast.In: lambda a, b: a in b,
        ast.NotIn: lambda a, b: a not in b,
    }

    def __init__(self, registry: UnitRegistry | None = None):
        self.registry = registry or UnitRegistry()

    def compile(self, expression: str) -> ast.Expression:
        try:
            tree = ast.parse(expression, mode='eval')
        except SyntaxError as exc:
            raise ExpressionError(f'invalid_policy_expression:{exc.msg}') from exc
        for node in ast.walk(tree):
            if not isinstance(node, self.ALLOWED):
                raise ExpressionError('unsafe_policy_expression')
        return tree

    def split_clauses(self, expression: str) -> tuple[str, ...]:
        """Split a top-level ``and`` expression into independent clauses."""
        tree = self.compile(expression)
        body = tree.body
        if isinstance(body, ast.BoolOp) and isinstance(body.op, ast.And):
            return tuple(ast.unparse(part) for part in body.values)
        return (expression.strip(),)

    def names(self, expression: str) -> tuple[str, ...]:
        tree = self.compile(expression)
        return tuple(sorted({node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}))

    def evaluate(self, expression: str, facts: Mapping[str, Any],
                 version_label: str = 'current') -> Evaluation:
        tree = self.compile(expression)
        names = tuple(sorted({node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}))
        clauses = tuple(self._trace_clause(clause_id, clause_text, facts)
                        for clause_id, clause_text in enumerate(self.split_clauses(expression), 1))
        invalid_clauses = [clause for clause in clauses if clause.result == INVALID]
        if invalid_clauses:
            reasons = tuple(reason for clause in invalid_clauses for reason in clause.reasons)
            return Evaluation(INVALID, expression, version_label, (), reasons, names, clauses, True)
        clause_states = {clause.result for clause in clauses}
        if INELIGIBLE in clause_states:
            # A decidable false settles a conjunction regardless of unknown
            # siblings; unknown must not be folded into ineligible.
            return Evaluation(INELIGIBLE, expression, version_label, (),
                              ('表达式计算结果为 false',), names, clauses)
        if ELIGIBLE in clause_states and UNKNOWN not in clause_states:
            return Evaluation(ELIGIBLE, expression, version_label, (),
                              ('表达式计算结果为 true',), names, clauses)
        if UNKNOWN in clause_states:
            missing = tuple(sorted({
                name for clause in clauses if clause.result == UNKNOWN
                for reason in clause.reasons for name in self._missing_name(reason)
            })) or tuple(name for name in names if name not in facts)
            reasons = tuple(f'缺少事实 {name}' for name in missing)
            return Evaluation(UNKNOWN, expression, version_label, missing, reasons, names, clauses)
        try:
            value = bool(self._eval(tree.body, facts))
        except _UnknownPropagation as propagation:
            reasons = (f'缺少事实 {propagation.name}',)
            return Evaluation(UNKNOWN, expression, version_label, (propagation.name,),
                              reasons, names, clauses)
        except UnitError as exc:
            return Evaluation(INVALID, expression, version_label, (), (str(exc),),
                              names, clauses, True)
        result = ELIGIBLE if value else INELIGIBLE
        return Evaluation(result, expression, version_label, (),
                          (f'表达式计算结果为 {str(value).lower()}',), names, clauses)

    @staticmethod
    def _missing_name(reason: str) -> tuple[str, ...]:
        marker = '缺少事实 '
        if marker not in reason:
            return ()
        return tuple(part.strip() for part in reason.split(marker, 1)[1].split(',') if part.strip())

    def _trace_clause(self, clause_id: int, expression: str,
                      facts: Mapping[str, Any]) -> ClauseTrace:
        label = f'clause-{clause_id}'
        tree = self.compile(expression)
        names = tuple(sorted({node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}))
        units = {name: self.registry.unit_of(name) for name in names
                 if self.registry.unit_of(name) is not None}
        missing = tuple(name for name in names if name not in facts)
        fact_values = {name: facts[name] for name in names if name in facts}
        try:
            value = bool(self._eval(tree.body, facts))
        except _UnknownPropagation as propagation:
            reasons = (f'缺少事实 {propagation.name}',)
            return ClauseTrace(label, expression, UNKNOWN, reasons,
                               names, fact_values, units, False)
        except UnitError as exc:
            return ClauseTrace(label, expression, INVALID, (str(exc),),
                               names, fact_values, units, False)
        except (TypeError, ValueError) as exc:
            return ClauseTrace(label, expression, INVALID, (str(exc),),
                               names, fact_values, units, False)
        # A missing name can coexist with a settled result when another
        # branch of an or/and expression decides the outcome.
        if missing and not value:
            return ClauseTrace(label, expression, UNKNOWN,
                               tuple(f'缺少事实 {name}' for name in missing),
                               names, fact_values, units, False)
        return ClauseTrace(label, expression, ELIGIBLE if value else INELIGIBLE,
                           (f'条件计算结果为 {str(value).lower()}',),
                           names, fact_values, units, True)

    def _eval(self, node: ast.AST, facts: Mapping[str, Any]) -> Any:
        if isinstance(node, ast.Expression):
            return self._eval(node.body, facts)
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id not in facts:
                raise _UnknownPropagation(node.id)
            return facts[node.id]
        if isinstance(node, ast.List):
            return [self._eval(item, facts) for item in node.elts]
        if isinstance(node, ast.Tuple):
            return tuple(self._eval(item, facts) for item in node.elts)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return not self._truth(node.operand, facts)
        if isinstance(node, ast.BoolOp):
            return self._eval_bool(node, facts)
        if isinstance(node, ast.Compare):
            return self._eval_compare(node, facts)
        raise ExpressionError('unsafe_policy_expression')

    def _truth(self, node: ast.AST, facts: Mapping[str, Any]) -> bool:
        return bool(self._eval(node, facts))

    def _eval_bool(self, node: ast.BoolOp, facts: Mapping[str, Any]) -> Any:
        # Three-valued logic: an unknown branch only propagates when the
        # known branches cannot settle the outcome.
        saw_unknown = False
        for part in node.values:
            try:
                value = bool(self._eval(part, facts))
            except _UnknownPropagation:
                saw_unknown = True
                continue
            if isinstance(node.op, ast.And) and not value:
                return False
            if isinstance(node.op, ast.Or) and value:
                return True
        if saw_unknown:
            raise _UnknownPropagation('subexpression')
        return isinstance(node.op, ast.And)

    def _eval_compare(self, node: ast.Compare, facts: Mapping[str, Any]) -> bool:
        # Each chained link compares the original neighbouring operands
        # (Python semantics: a < b < c == (a < b) and (b < c)); we carry the
        # raw value and its unit forward rather than the previous boolean.
        left_value = self._eval(node.left, facts)
        left_unit = self._node_unit(node.left)
        left_node = node.left
        result = True
        for operator, comparator_node in zip(node.ops, node.comparators):
            right_value = self._eval(comparator_node, facts)
            right_unit = self._node_unit(comparator_node)
            left_unit, right_unit = self._literal_units(
                left_node, comparator_node, left_unit, right_unit)
            if self._numeric(left_value) and self._numeric(right_value):
                left_number, right_number, _ = self.registry.reconcile(
                    left_unit, right_unit, left_value, right_value)
                link = self.COMPARISONS[type(operator)](left_number, right_number)
            else:
                link = self.COMPARISONS[type(operator)](left_value, right_value)
            result = result and link
            left_value = right_value
            left_unit = right_unit
            left_node = comparator_node
        return result

    def _literal_units(self, left_node: ast.AST, right_node: ast.AST,
                       left_unit: str | None, right_unit: str | None
                       ) -> tuple[str | None, str | None]:
        # A bare numeric literal compared against exactly one unit-bearing
        # fact is interpreted in that fact's unit (e.g. "age >= 18" means
        # 18 years).  Two unit-bearing facts still require equal units.
        if left_unit is None and right_unit is not None and isinstance(left_node, ast.Constant):
            return right_unit, right_unit
        if right_unit is None and left_unit is not None and isinstance(right_node, ast.Constant):
            return left_unit, left_unit
        return left_unit, right_unit

    def _node_unit(self, node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            return self.registry.unit_of(node.id)
        return None

    @staticmethod
    def _numeric(value: Any) -> bool:
        if isinstance(value, bool):
            return False
        if isinstance(value, (int, float, Decimal)):
            return True
        try:
            Decimal(str(value))
            return True
        except Exception:
            return False
