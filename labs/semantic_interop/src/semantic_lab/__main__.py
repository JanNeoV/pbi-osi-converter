import argparse
import json

from .fixtures import ROOT
from .runner import benchmark


def main():
    parser = argparse.ArgumentParser(description="Synthetic semantic interoperability evidence")
    parser.add_argument("command", choices=["benchmark", "diagnose", "diagnose-worker"])
    parser.add_argument("--check", action="store_true", help="Fail if milestone acceptance checks fail")
    parser.add_argument("--upstream-python", help="Already installed isolated current-upstream Python; never installs packages")
    parser.add_argument("--repair-python", help="Optional isolated repair environment")
    parser.add_argument("--capture", help="New portable evidence directory; existing directories are refused")
    args = parser.parse_args()
    if args.command == "benchmark":
        run, report = benchmark()
    else:
        from .diagnostics import diagnose, worker
        run, report = worker() if args.command == "diagnose-worker" else diagnose(args.upstream_python, args.repair_python)
    if args.capture:
        from .diagnostics import capture
        capture(run, args.capture)
    acceptance = report.get("task02_acceptance", report["acceptance"])
    print(json.dumps({"report": (run / "report.json").relative_to(ROOT).as_posix(),
                      "statuses": report["status_counts"], "acceptance": acceptance,
                      **({"summary": report["semantic_diagnostics"]["summary"]} if "semantic_diagnostics" in report else {})}, indent=2))
    return 1 if args.check and not acceptance["accepted"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
