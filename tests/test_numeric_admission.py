"""Numeric public-API controls, with independent Decimal/Fraction oracles."""
from decimal import Decimal, localcontext
from fractions import Fraction
from itertools import permutations

import numpy as np
import pytest
from test_integer_aggregates import aggregate_task, compiler_for

from aheaddb import Compiler


def exact(values):
    return sum((Fraction.from_float(float(v)) for v in values), Fraction())


@pytest.mark.parametrize("values", [
    [1.5, 2.25, -0.5], [0.1, 0.2, 0.3], [-2.0, -3.0, 1.0],
    [1e16, 1.0, -1e16], [1e308, 1e308, -1e308],
    [-1e308, -1e308, 1e308], [1e308, -1e308, 1.0],
])
def test_features_and_continuous_labels_match_independent_oracle(values):
    total = exact(values)
    with localcontext() as ctx:
        ctx.prec = 1000
        decimal_sum = sum(map(Decimal.from_float, values), Decimal())
        mean = decimal_sum / len(values)
        variance = sum(((Decimal.from_float(v) - mean)**2 for v in values), Decimal()) / len(values)
        spread = float(variance.sqrt())
    assert float(total) == float(decimal_sum)
    for ordered in set(permutations(values)):
        features = compiler_for(ordered, "float64").features(aggregate_task(), "2026-02-01")
        for prefix in ["events", "events__recent1", "events__recent7", "events__recent30"]:
            assert features.loc["seen", f"{prefix}__amount__sum"] == float(total)
            assert features.loc["seen", f"{prefix}__amount__mean"] == float(total / len(values))
        assert features.loc["seen", "events__amount__std"] == pytest.approx(spread, rel=2e-15)
        labels = compiler_for(ordered, "float64", labels=True)
        assert labels.labels(aggregate_task(), "2026-02-01", "2026-02-05").loc["seen"] == float(total)
        assert labels.labels(aggregate_task(binary=True), "2026-02-01", "2026-02-05").loc["seen"] == float(total > 0)
        assert np.isfinite(features.to_numpy()).all()


def test_binary_threshold_uses_exact_float_sum_before_rounding():
    compiler = compiler_for([float(2**53), 1.0, 0.0], "float64", labels=True)
    assert compiler.labels(aggregate_task(binary=True, threshold=2**53), "2026-02-01", "2026-02-05").loc["seen"] == 1
    assert compiler.labels(aggregate_task(binary=True, threshold=-1), "2026-02-01", "2026-02-05").loc["empty"] == 1


@pytest.mark.parametrize("values", [[1e308, 1e308, 0.0], [-1e308, -1e308, 0.0]])
def test_true_sum_overflow_refuses_float_outputs_but_preserves_binary_decision(values):
    with pytest.raises(ValueError, match="finite binary64"):
        compiler_for(values, "float64").features(aggregate_task(), "2026-02-01")
    compiler = compiler_for(values, "float64", labels=True)
    with pytest.raises(ValueError, match="finite binary64"):
        compiler.labels(aggregate_task(), "2026-02-01", "2026-02-05")
    assert compiler.labels(aggregate_task(binary=True), "2026-02-01", "2026-02-05").loc["seen"] == float(exact(values) > 0)


def test_representable_sum_but_unrepresentable_trend_refuses():
    with pytest.raises(ValueError, match="Derived feature"):
        compiler_for([-1.7e308, -1.7e308, 1.7e308], "float64").features(aggregate_task(), "2026-02-01")


@pytest.mark.parametrize("values", [[None, None, None], [1.5, None, 2.5]])
def test_missing_features_retained_but_missing_ground_truth_refused(values):
    frame = compiler_for(values, "Float64").features(aggregate_task(), "2026-02-01")
    assert frame.loc["seen", "events__amount__missing"] == values.count(None)
    assert frame.loc["seen", "events__amount__sum"] == sum(v for v in values if v is not None)
    assert (frame.loc["empty"].filter(like="amount") == 0).all()
    for binary in [False, True]:
        with pytest.raises(ValueError, match="Missing outcome"):
            compiler_for(values, "Float64", labels=True).labels(aggregate_task(binary=binary), "2026-02-01", "2026-02-05")


def test_empty_window_and_cutoff_are_not_arithmetic_failures():
    compiler = compiler_for([1e308, 1e308, -1e308], "float64")
    before = compiler.features(aggregate_task(), "2026-01-30")
    assert (before.filter(like="amount") == 0).all().all()
    after = compiler.features(aggregate_task(), "2026-02-02")
    assert after.loc["seen", "events__amount__sum"] == 1e308
    assert after.loc["seen", "events__recent1__amount__sum"] == 0
    assert after.loc["seen", "events__recent7__amount__sum"] == 1e308


def test_preprocessing_version_explicitly_changes_without_resealing_models():
    assert Compiler.PREPROCESSING.startswith("aggregates-v4:")
    assert "exact-binary-sum:finite-admission" in Compiler.PREPROCESSING
