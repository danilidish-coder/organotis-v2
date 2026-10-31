"""Γραμμή εντολών (βήμα 1: μόνο διαχείριση ρυθμίσεων)."""
from __future__ import annotations

import argparse
import json
import sys

from pathlib import Path

from . import __version__, config as cfgmod
from .formatting import format_plan, format_report
from .planner import build_plan
from .scanner import build_report, scan


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="organotis", description="Οργανωτής αρχείων")
    p.add_argument("--version", action="version", version=f"organotis {__version__}")
    sub = p.add_subparsers(dest="cmd")
    c = sub.add_parser("config", help="ρυθμίσεις αυτού του υπολογιστή")
    csub = c.add_subparsers(dest="config_cmd", required=True)
    csub.add_parser("path", help="πού βρίσκεται το αρχείο ρυθμίσεων")
    csub.add_parser("show", help="εμφάνιση τρεχουσών ρυθμίσεων")
    csub.add_parser("init", help="δημιουργία αρχείου ρυθμίσεων (δεν αντικαθιστά υπάρχον)")
    s = sub.add_parser("scan", help="σάρωση φακέλου και αναφορά (μόνο ανάγνωση)")
    s.add_argument("folder", nargs="?", help="φάκελος· αν λείπει, οι φάκελοι από τις ρυθμίσεις")
    pl = sub.add_parser("plan", help="πρόταση δομής με ορατούς κανόνες (δεν μετακινεί τίποτα)")
    pl.add_argument("folder", nargs="?", help="φάκελος· αν λείπει, οι φάκελοι από τις ρυθμίσεις")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.cmd not in ("config", "scan", "plan"):
        parser.print_help()
        return 0
    path = cfgmod.default_config_path()
    if args.cmd in ("scan", "plan"):
        try:
            cfg = cfgmod.load(path)
        except cfgmod.ConfigError as exc:
            print(f"Σφάλμα ρυθμίσεων: {exc}", file=sys.stderr)
            return 2
        roots = [args.folder] if args.folder else cfg.scan_roots
        if not roots:
            print("Δεν δόθηκε φάκελος και δεν υπάρχουν φάκελοι σάρωσης στις ρυθμίσεις.", file=sys.stderr)
            return 2
        for i, root in enumerate(roots):
            if i:
                print("\n" + "=" * 60 + "\n")
            result = scan(Path(root).expanduser(), cfg)
            report = build_report(result)
            if args.cmd == "scan":
                print(format_report(report))
            else:
                print(format_plan(build_plan(result, report, cfg)))
        return 0
    try:
        if args.config_cmd == "path":
            print(path)
        elif args.config_cmd == "show":
            print(json.dumps(cfgmod.load(path).to_dict(), ensure_ascii=False, indent=2))
        elif args.config_cmd == "init":
            if path.exists():
                print(f"Υπάρχει ήδη: {path}. Δεν αλλάζει τίποτα.")
                return 1
            cfgmod.save(cfgmod.Config(), path)
            print(f"Δημιουργήθηκε: {path}")
    except cfgmod.ConfigError as exc:
        print(f"Σφάλμα ρυθμίσεων: {exc}", file=sys.stderr)
        return 2
    return 0
