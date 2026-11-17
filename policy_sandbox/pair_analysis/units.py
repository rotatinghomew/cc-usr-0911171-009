from __future__ import annotations

import dataclasses
from decimal import Decimal
from typing import Any, Mapping


class UnitError(ValueError):
    """Raised when values carrying incompatible units are compared directly."""


@dataclasses.dataclass(frozen=True)
class UnitRegistry:
    """Maps fact names to their declared units and converts compatible units.

    Two values may only be compared when their units are identical after
    conversion.  A fact without a declared unit is treated as a bare scalar,
    and a bare scalar can never be compared against a unit-bearing value.
    """

    units: Mapping[str, str] = dataclasses.field(default_factory=dict)
    conversions: Mapping[str, Mapping[str, Decimal]] = dataclasses.field(default_factory=dict)

    def unit_of(self, name: str) -> str | None:
        return self.units.get(name)

    def converted(self, value: Decimal, source_unit: str | None, target_unit: str) -> Decimal:
        if source_unit == target_unit:
            return value
        table = self.conversions.get(source_unit or '', {})
        if target_unit not in table:
            raise UnitError(f'unit_mismatch:{source_unit}:{target_unit}')
        return value * table[target_unit]

    def reconcile(self, left_unit: str | None, right_unit: str | None,
                  left_value: Any, right_value: Any) -> tuple[Decimal, Decimal, str]:
        """Return both values expressed in one common unit.

        A bare scalar on exactly one side is rejected so that e.g. an income
        in CNY is never compared directly with an unlabelled number.
        """
        left = Decimal(str(left_value))
        right = Decimal(str(right_value))
        if left_unit is None and right_unit is None:
            return left, right, ''
        if left_unit is None or right_unit is None:
            raise UnitError(f'unit_mismatch:{left_unit}:{right_unit}')
        if left_unit == right_unit:
            return left, right, left_unit
        if right_unit in self.conversions.get(left_unit, {}):
            return left, right * self.conversions[left_unit][right_unit], left_unit
        if left_unit in self.conversions.get(right_unit, {}):
            return left * self.conversions[right_unit][left_unit], right, right_unit
        raise UnitError(f'unit_mismatch:{left_unit}:{right_unit}')
