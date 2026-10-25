import copy
from pathlib import Path

import numpy as np
import pytest

from lease_plan_check.audit import read_json
from lease_plan_check.commands import asset
from lease_plan_check.engine import Calibration, Matter, analyse, calibrate

ROOT = Path(__file__).parents[1] / "examples/retail-premises"


def sample():
    return Matter.model_validate(read_json(ROOT / "matter.json"))


def run(matter):
    return analyse(matter, (ROOT / "lease.txt").read_bytes(), (ROOT / "plan.png").read_bytes())


def test_realistic_discrepancies_and_annotated_output():
    report, png, geojson = run(sample())
    measurements = {r["claim"]: r for r in report["measurements"]}
    assert measurements["shop-area"]["observed"] == pytest.approx(600)
    assert measurements["shop-area"]["expected"] == 650
    assert measurements["terrace-inclusion"]["observed"] == pytest.approx(200)
    assert measurements["frontage-length"]["observed"] == pytest.approx(30)
    assert measurements["service-access"]["observed"] > 0
    assert all(r["mismatch"] for r in measurements.values())
    assert png.startswith(b"\x89PNG")
    assert len(geojson["features"]) == 5
    assert len(report["findings"]) == 8


def test_affine_rotation_and_scale():
    config = Calibration(
        controls=[
            {"pixel": [0, 0], "metres": [10, 20]},
            {"pixel": [10, 0], "metres": [10, 40]},
            {"pixel": [0, 10], "metres": [-10, 20]},
            {"pixel": [10, 10], "metres": [-10, 40]},
        ]
    )
    matrix, stats = calibrate(config)
    assert np.array([5, 5, 1]) @ matrix == pytest.approx([0, 30])
    assert stats["max_residual_m"] < 1e-9


def test_collinear_controls_refused():
    config = Calibration(controls=[{"pixel": [i, i], "metres": [i, i]} for i in range(3)])
    with pytest.raises(ValueError, match="collinear"):
        calibrate(config)


def test_contradictory_controls_refused():
    matter = sample()
    matter.calibration.controls[-1].metres = (100, 100)
    with pytest.raises(ValueError, match="Contradictory"):
        run(matter)


@pytest.mark.parametrize(
    "problem", ["outside", "bowtie", "duplicate", "missing", "nonfinite", "units"]
)
def test_invalid_matter(problem):
    raw = read_json(ROOT / "matter.json")
    if problem == "outside":
        raw["features"][0]["points"][0] = [-1, 200]
    elif problem == "bowtie":
        raw["features"][0]["points"] = [[100, 200], [700, 600], [700, 200], [100, 600]]
    elif problem == "duplicate":
        raw["features"].append(copy.deepcopy(raw["features"][0]))
    elif problem == "missing":
        raw["claims"][0]["feature"] = "missing"
    elif problem == "nonfinite":
        raw["features"][0]["points"][0] = [float("nan"), 200]
    else:
        raw["calibration"]["units"] = "feet"
    with pytest.raises(ValueError):
        run(Matter.model_validate(raw))


def test_wrong_image_hash_refused():
    matter = sample()
    with pytest.raises(ValueError, match="bytes changed"):
        analyse(matter, (ROOT / "lease.txt").read_bytes(), (ROOT / "plan.png").read_bytes() + b" ")


def test_bad_quote_refused():
    matter = sample()
    matter.claims[0].evidence.end += 1
    with pytest.raises(ValueError, match="span"):
        run(matter)


def test_unsupported_geometry_claim_refused():
    matter = sample()
    matter.claims[0].feature = "frontage"
    with pytest.raises(ValueError, match="polygon"):
        run(matter)


def test_exactly_three_controls_are_flagged():
    matter = sample()
    matter.calibration.controls.pop()
    report, _, _ = run(matter)
    assert any(r["rule"] == "calibration-check" for r in report["findings"])


def test_thresholds_can_be_corrected_without_changing_old_report():
    matter = sample()
    matter.claims[0].stated = 600
    matter.claims[2].stated = 30
    report, _, _ = run(matter)
    ids = {r["entity"] for r in report["findings"] if r["rule"] == "geometry-discrepancy"}
    assert "shop-area" not in ids and "frontage-length" not in ids


def test_asset_paths_confined(tmp_path):
    with pytest.raises(ValueError, match="inside"):
        asset(tmp_path, "../outside.txt")


def test_duplicate_control_is_not_an_independent_check():
    matter = sample()
    matter.calibration.controls[-1] = matter.calibration.controls[0]
    with pytest.raises(ValueError, match="Duplicate pixel"):
        run(matter)


def test_boolean_coordinate_refused():
    raw = read_json(ROOT / "matter.json")
    raw["features"][0]["points"][0] = [True, 200]
    with pytest.raises(ValueError):
        Matter.model_validate(raw)


def test_boolean_evidence_offset_refused():
    raw = read_json(ROOT / "matter.json")
    raw["claims"][0]["evidence"]["start"] = True
    with pytest.raises(ValueError):
        Matter.model_validate(raw)
