"""Casos felizes, bordas e recuperação do DAG persistente."""

from __future__ import annotations

import pytest

from backend.scheduling.dag import (
    DAGStage,
    DAGValidationError,
    validate_stages,
)


def test_validate_rejects_cycle_and_unknown_dependency() -> None:
    with pytest.raises(DAGValidationError):
        validate_stages([DAGStage("a", "A", ("b",)), DAGStage("b", "B", ("a",))])
    with pytest.raises(DAGValidationError):
        validate_stages([DAGStage("a", "A", ("missing",))])
    with pytest.raises(DAGValidationError):
        validate_stages([DAGStage("a", "A", ("b", "b")), DAGStage("b", "B")])
