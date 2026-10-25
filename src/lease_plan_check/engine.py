"""Local planar measurements and reviewable reconciliation; no cadastral inference."""

from __future__ import annotations

import base64
import io
from importlib.metadata import version
from typing import Literal

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from pydantic import Field, StrictBool, StrictFloat, model_validator
from shapely.geometry import LineString, Polygon
from shapely.validation import explain_validity

from .audit import Strict, check_span, finding

Point = tuple[StrictFloat, StrictFloat]


class Reference(Strict):
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    quote: str = Field(min_length=1)
    locator: str = Field(min_length=1)


class Asset(Strict):
    file: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class Control(Strict):
    pixel: Point
    metres: Point


class Calibration(Strict):
    units: Literal["metres"] = "metres"
    controls: list[Control] = Field(min_length=3)
    max_residual_m: float = Field(default=0.1, gt=0)


class Feature(Strict):
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,62}$")
    label: str = Field(min_length=1)
    kind: Literal["polygon", "line"]
    points: list[Point] = Field(min_length=2)
    provenance: str = Field(min_length=8)


class Claim(Strict):
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,62}$")
    kind: Literal["area", "length", "includes", "excludes", "access-clear"]
    feature: str
    other: str | None = None
    stated: float | None = Field(default=None, ge=0)
    tolerance: float = Field(default=0.1, ge=0)
    route_width_m: float = Field(default=1.0, gt=0)
    evidence: Reference
    interpretation: Literal["proposed", "human-confirmed"] = "proposed"
    reviewer: str | None = None

    @model_validator(mode="after")
    def shape(self):
        if self.kind in {"area", "length"} and (self.stated is None or self.other is not None):
            raise ValueError("A measurement claim needs stated value and no second feature")
        if self.kind not in {"area", "length"} and (not self.other or self.stated is not None):
            raise ValueError("A relationship claim needs a second feature and no numeric statement")
        if self.interpretation == "human-confirmed" and not (self.reviewer or "").strip():
            raise ValueError("Confirmed lease readings need reviewer attribution")
        return self


class Matter(Strict):
    schema_version: Literal["1"] = "1"
    matter: str = Field(min_length=1)
    synthetic: StrictBool = False
    plan: Asset
    lease: Asset
    calibration: Calibration
    features: list[Feature] = Field(min_length=1)
    claims: list[Claim] = Field(min_length=1)

    @model_validator(mode="after")
    def references(self):
        for items in (self.features, self.claims):
            ids = [v.id for v in items]
            if len(set(ids)) != len(ids):
                raise ValueError("Duplicate feature/claim IDs")
        ids = {f.id for f in self.features}
        for claim in self.claims:
            if claim.feature not in ids or (claim.other is not None and claim.other not in ids):
                raise ValueError("Claim refers to a missing geometry feature")
        return self


def calibrate(config):
    if len({c.pixel for c in config.controls}) != len(config.controls):
        raise ValueError("Duplicate pixel controls do not provide an independent calibration check")
    pixels = np.array([[*c.pixel, 1.0] for c in config.controls])
    metres = np.array([c.metres for c in config.controls])
    transform, _, rank, _ = np.linalg.lstsq(pixels, metres, rcond=None)
    if rank != 3 or abs(np.linalg.det(transform[:2])) < 1e-12:
        raise ValueError("Calibration controls are collinear or produce a collapsed transform")
    if np.linalg.cond(pixels) > 1e10:
        raise ValueError("Calibration is ill-conditioned; choose well-spaced controls")
    residuals = np.linalg.norm(pixels @ transform - metres, axis=1)
    if max(residuals) > config.max_residual_m:
        raise ValueError("Contradictory calibration controls exceed the residual tolerance")
    return transform, {
        "matrix_pixel_to_metres": transform.tolist(),
        "residuals_m": residuals.tolist(),
        "max_residual_m": float(max(residuals)),
        "control_count": len(config.controls),
        "independent_accuracy": "Not established; fit residuals are not survey accuracy",
    }


def geometry(feature, transform, width, height):
    if any(x < 0 or y < 0 or x > width or y > height for x, y in feature.points):
        raise ValueError(f"Feature {feature.id} lies outside the supplied plan")
    points = np.array([[x, y, 1] for x, y in feature.points]) @ transform
    if feature.kind == "polygon":
        if len(points) < 3:
            raise ValueError("Polygon needs at least three vertices")
        shape = Polygon(points)
    else:
        shape = LineString(points)
    if (
        shape.is_empty
        or not shape.is_valid
        or shape.length <= 1e-10
        or (feature.kind == "polygon" and shape.area <= 1e-10)
    ):
        raise ValueError(f"Invalid geometry {feature.id}: {explain_validity(shape)}")
    if feature.kind == "line" and not shape.is_simple:
        raise ValueError(f"Self-crossing line {feature.id} needs correction")
    return shape


def analyse(matter, lease_raw, plan_raw):
    from .audit import digest

    if digest(lease_raw) != matter.lease.sha256 or digest(plan_raw) != matter.plan.sha256:
        raise ValueError("Lease or plan bytes changed; revise the input references")
    lease_text = lease_raw.decode("utf-8-sig")
    with Image.open(io.BytesIO(plan_raw)) as source:
        if source.format != "PNG" or source.width * source.height > 30_000_000:
            raise ValueError("Use a PNG plan of at most 30 million pixels")
        image = source.convert("RGB")
    width, height = image.size
    if any(
        not (0 <= c.pixel[0] <= width and 0 <= c.pixel[1] <= height)
        for c in matter.calibration.controls
    ):
        raise ValueError("Calibration control lies outside the plan")
    transform, calibration = calibrate(matter.calibration)
    shapes = {f.id: geometry(f, transform, width, height) for f in matter.features}
    rows, measurements, flagged = [], [], set()
    for claim in matter.claims:
        check_span(claim.evidence.model_dump(), lease_text)
        shape, other = shapes[claim.feature], shapes.get(claim.other)
        refs = [
            {"source": "lease.txt", **claim.evidence.model_dump()},
            {
                "source": "matter.json",
                "locator": f"claim[id={claim.id}]; feature[id={claim.feature}]",
            },
            {
                "source": "plan.png",
                "locator": next(f.provenance for f in matter.features if f.id == claim.feature),
            },
        ]
        mismatch, observed, expected = False, None, claim.stated
        if claim.kind == "area":
            if shape.geom_type != "Polygon":
                raise ValueError("Area claim needs a polygon")
            observed = shape.area
            mismatch = abs(observed - claim.stated) > claim.tolerance + 1e-9
        elif claim.kind == "length":
            if shape.geom_type != "LineString":
                raise ValueError("Length claim needs a labelled line, not a polygon perimeter")
            observed = shape.length
            mismatch = abs(observed - claim.stated) > claim.tolerance + 1e-9
        elif claim.kind in {"includes", "excludes"}:
            if shape.geom_type != "Polygon" or other.geom_type != "Polygon":
                raise ValueError("Inclusion/exclusion needs two polygons")
            observed = (
                other.difference(shape).area
                if claim.kind == "includes"
                else other.intersection(shape).area
            )
            expected = 0.0
            mismatch = observed > claim.tolerance + 1e-9
        else:
            if shape.geom_type != "LineString" or other.geom_type != "Polygon":
                raise ValueError("Access check needs a route line and an obstacle polygon")
            observed = shape.buffer(claim.route_width_m / 2).intersection(other).area
            expected = 0.0
            mismatch = observed > claim.tolerance + 1e-9
        measurements.append(
            {
                "claim": claim.id,
                "kind": claim.kind,
                "expected": expected,
                "observed": float(observed),
                "tolerance": claim.tolerance,
                "mismatch": bool(mismatch),
                "evidence": refs,
            }
        )
        if claim.interpretation == "proposed":
            rows.append(
                finding(
                    "lease-reading",
                    claim.id,
                    f"Confirm the lease reading: {claim.id}",
                    after=claim.model_dump(mode="json"),
                    evidence=refs,
                    classification="judgment",
                    explanation="The supplied reading and digitised extent require review against the lease and plan.",
                )
            )
        if mismatch:
            flagged.add(claim.feature)
            if claim.other:
                flagged.add(claim.other)
            rows.append(
                finding(
                    "geometry-discrepancy",
                    claim.id,
                    f"{claim.id}: {claim.kind} differs from the lease reading",
                    before={"expected": expected, "tolerance": claim.tolerance},
                    after={"measured": round(float(observed), 6), "kind": claim.kind},
                    evidence=refs,
                    classification="calculated",
                    explanation="Local planar measurement using supplied controls and digitised features. "
                    "Tolerance is absolute: m² for area/overlap, metres for line length.",
                )
            )
    if len(matter.calibration.controls) == 3:
        rows.append(
            finding(
                "calibration-check",
                "controls",
                "Three controls provide no independent fit check",
                after=calibration,
                evidence=[{"source": "matter.json", "locator": "calibration.controls"}],
                classification="assumption",
                explanation="An affine transform exactly fits three non-collinear controls; use additional checks.",
            )
        )
    canvas = image.convert("RGBA")
    overlay = Image.new("RGBA", canvas.size)
    draw = ImageDraw.Draw(overlay)
    for feature in matter.features:
        points = [(float(x), float(y)) for x, y in feature.points]
        color = (173, 62, 36, 220) if feature.id in flagged else (31, 90, 121, 220)
        if feature.kind == "polygon":
            draw.polygon(points, fill=(*color[:3], 45), outline=color, width=3)
        else:
            draw.line(points, fill=color, width=4)
        if feature.kind == "polygon":
            x = min(p[0] for p in points) + 8
            y = min(p[1] for p in points) + 28
        else:
            midpoint = LineString(points).interpolate(0.5, normalized=True)
            x, y = midpoint.x, max(35, midpoint.y - 25)
        caption = feature.id + (" [review]" if feature.id in flagged else "")
        draw.text(
            (x + 5, y + 5),
            caption,
            fill=(20, 35, 45, 255),
            stroke_width=2,
            stroke_fill=(255, 255, 255, 255),
            font=ImageFont.load_default(size=17),
        )
    canvas = Image.alpha_composite(canvas, overlay).convert("RGB")
    # The annotation can never be mistaken for a certified survey plan.
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((0, 0, width, 34), fill="white")
    draw.text(
        (12, 10),
        "REVIEW ANNOTATION - supplied geometry; not a certified boundary survey",
        fill="black",
        font=ImageFont.load_default(size=17),
    )
    stream = io.BytesIO()
    canvas.save(stream, format="PNG")
    png = stream.getvalue()
    geometry_export = {
        "schema_version": "1",
        "coordinate_space": {"type": "local-engineering", "units": "metres", "authority": None},
        "features": [
            {
                "id": f.id,
                "properties": {"label": f.label, "provenance": f.provenance},
                "geometry_wkt": shapes[f.id].wkt,
            }
            for f in matter.features
        ],
    }
    report = {
        "tool": "lease-plan-check",
        "title": f"Premises reconciliation — {matter.matter}",
        "summary": f"{len(matter.claims)} lease claims checked against {len(matter.features)} supplied features",
        "synthetic": matter.synthetic,
        "review_status": "unreviewed",
        "findings": rows,
        "configuration": {
            "calibration": matter.calibration.model_dump(mode="json"),
            "fit": calibration,
            "coordinate_space": "local planar metres",
            "libraries": {name: version(name) for name in ("numpy", "shapely", "pillow")},
        },
        "coverage": {
            "claims": len(matter.claims),
            "features": len(matter.features),
            "whole_lease_extraction": False,
        },
        "measurements": measurements,
        "plan_preview": "data:image/png;base64," + base64.b64encode(png).decode(),
        "assumptions": [
            "Features and controls were supplied, not inferred from raster pixels.",
            "A good calibration fit does not establish survey accuracy or title boundaries.",
            "Only enumerated claims are assessed; unlisted lease provisions are outside scope.",
        ],
        "boundary": "Geometric findings are calculated from supplied evidence. Lease readings remain "
        "reviewable judgments. This is not a boundary survey or title certification.",
    }
    return report, png, geometry_export
