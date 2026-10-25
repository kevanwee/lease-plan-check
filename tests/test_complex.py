from pathlib import Path

import pytest

from lease_plan_check.audit import read_json
from lease_plan_check.engine import Matter, analyse

CASE = Path(__file__).parents[1] / "examples/irregular-premises"


def run(matter=None):
    matter = matter or Matter.model_validate(read_json(CASE / "matter.json"))
    return analyse(matter, (CASE / "lease.txt").read_bytes(), (CASE / "plan.png").read_bytes())[0]


def test_concave_plan_and_affine_shear_match_independent_math():
    report = run()
    expected = read_json(CASE / "expected.json")
    measured = {row["claim"]: row for row in report["measurements"]}
    for claim, value in expected["measurements"].items():
        assert measured[claim]["observed"] == pytest.approx(value, abs=1e-7)
    assert measured[expected["positive_overlap"]]["observed"] > 0
    assert {cid for cid, row in measured.items() if row["mismatch"]} == set(expected["mismatches"])
    assert {cid for cid, row in measured.items() if not row["mismatch"]} == set(expected["matches"])
    assert report["configuration"]["fit"]["control_count"] == 6
    assert report["configuration"]["fit"]["max_residual_m"] < 1e-9


@pytest.mark.parametrize("field", ["stated", "tolerance", "route_width_m"])
def test_boolean_measurements_are_not_numbers(field):
    raw = read_json(CASE / "matter.json")
    raw["claims"][0][field] = True
    with pytest.raises(ValueError):
        Matter.model_validate(raw)


def test_boolean_calibration_tolerance_refused():
    raw = read_json(CASE / "matter.json")
    raw["calibration"]["max_residual_m"] = True
    with pytest.raises(ValueError):
        Matter.model_validate(raw)


def test_inconsistent_sixth_control_is_refused():
    raw = read_json(CASE / "matter.json")
    raw["calibration"]["controls"][-1]["metres"] = [100, 100]
    with pytest.raises(ValueError, match="Contradictory"):
        run(Matter.model_validate(raw))


def test_polygon_holes_are_explicitly_unsupported():
    raw = read_json(CASE / "matter.json")
    raw["features"][0]["holes"] = [[[150, 200], [250, 200], [250, 260], [150, 260]]]
    with pytest.raises(ValueError):
        Matter.model_validate(raw)


def test_bowtie_digitisation_is_refused():
    raw = read_json(CASE / "matter.json")
    raw["features"][0]["points"] = [[100, 100], [700, 600], [700, 100], [100, 600]]
    with pytest.raises(ValueError, match="Invalid geometry"):
        run(Matter.model_validate(raw))


def test_complex_rehearsal_keeps_unresolved_questions_pending(tmp_path):
    import importlib.util

    root = Path(__file__).parents[1]
    spec = importlib.util.spec_from_file_location("rehearsal", root / "scripts/rehearse_complex.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.run("scripts/complex-scenario.json", tmp_path / "rehearsal")
    assert result["review_status"] == "needs-information"
    assert result["pending"] > 0
    assert result["external_actions"] == "none"
