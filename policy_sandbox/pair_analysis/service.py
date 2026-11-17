from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any, Mapping, Sequence

from .analyzer import PolicyPairAnalyzer
from .evaluator import SafeEvaluator
from .models import (
    FactSnapshot, PairAnalysisError, PairAnalysisReport, PolicyVersion,
)
from .reconcile import AnalysisReconciler
from .units import UnitRegistry

__all__ = [
    'PairAnalysisService', 'PolicyPairAnalyzer', 'SafeEvaluator',
    'FactSnapshot', 'PolicyVersion', 'PairAnalysisReport',
    'PairAnalysisError', 'UnitError', 'UnitRegistry', 'AnalysisReconciler',
]


class PairAnalysisService:
    """Application-facing entry point for paired policy analysis.

    Accepts plain mappings (fixture/CLI style), pins one fact snapshot, and
    interprets each policy version independently.  Nothing is persisted.
    """

    def __init__(self) -> None:
        self.analyzer = PolicyPairAnalyzer()
        self.reconciler = AnalysisReconciler()

    def run(self, payload: Mapping[str, Any]) -> PairAnalysisReport:
        snapshot = self._snapshot(payload)
        old = PolicyVersion.from_mapping('old', self._require(payload, 'old_policy'))
        new = PolicyVersion.from_mapping('new', self._require(payload, 'new_policy'))
        registry = UnitRegistry(snapshot.units,
                                self._conversions(payload.get('conversions', {})))
        self.analyzer.evaluator = SafeEvaluator(registry)
        self._validate_versions(old, new)
        return self.analyzer.analyze(snapshot, old, new)

    def run_verified(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        report = self.run(payload)
        problems = self.reconciler.verify(report)
        if problems:
            raise PairAnalysisError('reconciliation failed: ' + ','.join(problems))
        return report.as_dict() | {
            'reconciliation': {'status': 'balanced', 'problems': []},
            'persons': [self.reconciler.explain_person(item)
                        for item in report.person_comparisons],
        }

    def _snapshot(self, payload: Mapping[str, Any]) -> FactSnapshot:
        rows = payload.get('persons') or payload.get('items') or []
        if not isinstance(rows, Sequence) or not rows:
            raise PairAnalysisError('snapshot requires a non-empty persons list')
        units = payload.get('units', {})
        captured_at = payload.get('captured_at')
        moment = None
        if captured_at:
            moment = dt.datetime.fromisoformat(str(captured_at).replace('Z', '+00:00'))
        return FactSnapshot.build(rows, units=units, captured_at=moment)

    @staticmethod
    def _require(payload: Mapping[str, Any], key: str) -> Mapping[str, Any]:
        value = payload.get(key)
        if not isinstance(value, Mapping):
            raise PairAnalysisError(f'{key} is required')
        return value

    @staticmethod
    def _conversions(raw: Mapping[str, Any]) -> dict[str, dict[str, Decimal]]:
        result: dict[str, dict[str, Decimal]] = {}
        for source, targets in raw.items():
            if not isinstance(targets, Mapping):
                raise PairAnalysisError('conversions must map unit to units')
            result[source] = {target: Decimal(str(rate)) for target, rate in targets.items()}
        return result

    @staticmethod
    def _validate_versions(old: PolicyVersion, new: PolicyVersion) -> None:
        # Each version is interpreted on its own effective period; the new
        # version must not take effect before the one it replaces.
        if new.effective_from < old.effective_from:
            raise PairAnalysisError('new policy cannot predate the old policy')
