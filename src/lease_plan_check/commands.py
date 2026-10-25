from pathlib import Path

from .audit import Decisions, canonical, read_json
from .cli import analysis_args
from .engine import Matter, analyse


def arguments(sub):
    p = sub.add_parser("check", help="reconcile lease claims and calibrated plan geometry")
    p.add_argument("matter", type=Path)
    analysis_args(p)


def schemas():
    return {"matter": Matter, "decisions": Decisions}


def asset(directory, value):
    path = (directory / value).resolve()
    if not path.is_relative_to(directory.resolve()):
        raise ValueError("Assets must be inside the matter directory")
    return path.read_bytes()


def execute(args):
    matter = Matter.model_validate(read_json(args.matter))
    lease_raw = asset(args.matter.parent, matter.lease.file)
    plan_raw = asset(args.matter.parent, matter.plan.file)
    report, png, geometry_export = analyse(matter, lease_raw, plan_raw)
    return (
        report,
        {"matter.json": args.matter.read_bytes(), "lease.txt": lease_raw, "plan.png": plan_raw},
        {
            "annotated-plan.png": png,
            "geometry.local.json": canonical(geometry_export),
            "measurements.json": canonical(report["measurements"]),
        },
    )
