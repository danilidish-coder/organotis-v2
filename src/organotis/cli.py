"""Γραμμή εντολών (βήμα 1: μόνο διαχείριση ρυθμίσεων)."""
from __future__ import annotations

import argparse
import json
import sys

from . import __version__, config as cfgmod


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="organotis", description="Οργανωτής αρχείων")
    p.add_argument("--version", action="version", version=f"organotis {__version__}")
    sub = p.add_subparsers(dest="cmd")
    c = sub.add_parser("config", help="ρυθμίσεις αυτού του υπολογιστή")
    csub = c.add_subparsers(dest="config_cmd", required=True)
    csub.add_parser("path", help="πού βρίσκεται το αρχείο ρυθμίσεων")
    csub.add_parser("show", help="εμφάνιση τρεχουσών ρυθμίσεων")
    csub.add_parser("init", help="δημιουργία αρχείου ρυθμίσεων (δεν αντικαθιστά υπάρχον)")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.cmd != "config":
        parser.print_help()
        return 0
    path = cfgmod.default_config_path()
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
