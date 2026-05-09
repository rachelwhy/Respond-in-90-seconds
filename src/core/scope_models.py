from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class FieldConstraint:
    """单字段约束，供 scope 执行器统一消费。"""

    field_hint: str
    op: str
    value: Any
    value_type: str = "text"


@dataclass(frozen=True)
class ConstraintClause:
    """一个 AND 子句；ConstraintSet 的多个子句之间是 OR 关系。"""

    constraints: List[FieldConstraint]
    scope_hint: Optional[str] = None


@dataclass(frozen=True)
class ConstraintSet:
    """指令编译后的统一约束结构。"""

    predicate: str
    compiler: str
    clauses: List[ConstraintClause]
    priority: int
    metadata: Dict[str, Any] = field(default_factory=dict)
