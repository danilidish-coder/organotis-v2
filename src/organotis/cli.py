"""Γραμμή εντολών (βήμα 1: μόνο διαχείριση ρυθμίσεων)."""
from __future__ import annotations

import argparse
import json
import sys

from pathlib import Path

from . import __version__, config as cfgmod
from .approval import PreviewModel, parse_numbers
from .formatting import (format_plan, format_preview, format_report, format_runs,
                         format_undo_preview)
from .mover import execute
from .planner import build_plan
from .scanner import build_report, scan
from .undo import list_runs, pending_ops, undo as run_undo
from . import manifest as mfmod


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
    mv = sub.add_parser("move", help="προεπισκόπηση και μετακίνηση με manifest (ζητά έγκριση)")
    mv.add_argument("folder", nargs="?", help="φάκελος· αν λείπει, ο πρώτος από τις ρυθμίσεις")
    mv.add_argument("--remove", default="", metavar="ΑΡΙΘΜΟΙ",
                    help="γραμμές που αφαιρούνται από την προεπισκόπηση, π.χ. 1,3,5-7")
    mv.add_argument("--yes", action="store_true",
                    help="έγκριση χωρίς ερώτηση (η προεπισκόπηση τυπώνεται πάντα πρώτα)")
    un = sub.add_parser("undo", help="αναίρεση εκτέλεσης από το manifest (δουλεύει και μετά από επανεκκίνηση)")
    un.add_argument("run", nargs="?", help="κωδικός εκτέλεσης· αν λείπει, εμφανίζεται η λίστα")
    un.add_argument("--last", action="store_true", help="η πιο πρόσφατη εκτέλεση")
    un.add_argument("--yes", action="store_true", help="έγκριση χωρίς ερώτηση")
    un.add_argument("--include-modified", action="store_true",
                    help="επιστροφή και αρχείων που άλλαξαν μετά τη μετακίνηση")
    sub.add_parser("gui", help="γραφικό περιβάλλον")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.cmd is None or args.cmd == "gui":
        return _gui_or_help(parser, bare=args.cmd is None)
    path = cfgmod.default_config_path()
    if args.cmd == "move":
        return _move(args, path)
    if args.cmd == "undo":
        return _undo(args, path)
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


def _gui_or_help(parser: argparse.ArgumentParser, bare: bool) -> int:
    try:
        from .gui import run
    except ImportError as exc:  # λείπει το tkinter
        if not bare:
            print(f"Το γραφικό περιβάλλον δεν είναι διαθέσιμο: {exc}", file=sys.stderr)
            return 2
        parser.print_help()
        return 0
    return run()


def _move(args, config_path: Path) -> int:
    try:
        cfg = cfgmod.load(config_path)
    except cfgmod.ConfigError as exc:
        print(f"Σφάλμα ρυθμίσεων: {exc}", file=sys.stderr)
        return 2
    folder = args.folder or (cfg.scan_roots[0] if cfg.scan_roots else None)
    if not folder:
        print("Δεν δόθηκε φάκελος και δεν υπάρχουν φάκελοι σάρωσης στις ρυθμίσεις.", file=sys.stderr)
        return 2
    result = scan(Path(folder).expanduser(), cfg)
    if not result.entries:
        print("Δεν βρέθηκαν αρχεία." + "".join(f"\n  ! {p.reason}" for p in result.problems))
        return 1
    model = PreviewModel(build_plan(result, build_report(result), cfg))
    if not model.rows:
        print("Δεν υπάρχει τίποτα να μετακινηθεί: όλα μένουν στη θέση τους.")
        return 0
    try:
        model.remove(parse_numbers(args.remove))
    except ValueError as exc:
        print(f"Σφάλμα: {exc}", file=sys.stderr)
        return 2
    interactive = sys.stdin.isatty() and not args.yes
    print(format_preview(model))
    if interactive:
        try:
            while True:
                ans = input("\nΑριθμοί γραμμών που ΔΕΝ θέλεις (π.χ. 1,3,5-7) ή Enter για συνέχεια: ").strip()
                if not ans:
                    break
                try:
                    model.remove(parse_numbers(ans))
                except ValueError as exc:
                    print(exc)
                    continue
                print(f"Έμειναν {len(model.active)} από {len(model.rows)} γραμμές.")
            if not model.active:
                print("Δεν έμεινε τίποτα. Δεν μετακινήθηκε τίποτα.")
                return 0
            if input(f"\nΝα μετακινηθούν {len(model.active)} αρχεία; Γράψε ΝΑΙ για να γίνει: ").strip() != "ΝΑΙ":
                print("Ακυρώθηκε. Δεν μετακινήθηκε τίποτα.")
                return 1
        except (EOFError, KeyboardInterrupt):
            print("\nΑκυρώθηκε. Δεν μετακινήθηκε τίποτα.")
            return 1
    elif not args.yes:
        print("\nΧρειάζεται έγκριση. Τρέξε την εντολή σε τερματικό (θα σε ρωτήσει) "
              "ή πρόσθεσε --yes αφού δεις την προεπισκόπηση. Δεν μετακινήθηκε τίποτα.", file=sys.stderr)
        return 1
    try:
        approved = model.approve()
    except ValueError as exc:
        print(f"Σφάλμα: {exc}", file=sys.stderr)
        return 2
    res = execute(approved, cfg.manifest_path(config_path))
    for note in res.recovered_notes:
        print(f"Ανάκτηση από προηγούμενη διακοπή: {note}")
    print(f"\nΜετακινήθηκαν {res.moved} αρχεία, παραλείφθηκαν {len(res.skipped)}."
          f"{' (Διακόπηκε.)' if res.status == 'stopped' else ''}")
    for p, why in res.skipped:
        print(f"  ! {p.entry.path.name}: {why}")
    print(f"Manifest (για αναίρεση): {res.manifest_path}")
    return 0 if not res.skipped else 3


def _undo(args, config_path: Path) -> int:
    try:
        cfg = cfgmod.load(config_path)
    except cfgmod.ConfigError as exc:
        print(f"Σφάλμα ρυθμίσεων: {exc}", file=sys.stderr)
        return 2
    mdir = cfg.manifest_path(config_path)
    runs = list_runs(mdir)
    if not args.run and not args.last:
        print(format_runs(runs))
        if runs:
            print("\nΓια αναίρεση: organotis undo --last   ή   organotis undo ΚΩΔΙΚΟΣ")
        return 0
    if args.run:
        matches = [r for r in runs if r.run_id == args.run]
        if not matches:
            print(f"Δεν βρέθηκε εκτέλεση «{args.run}».", file=sys.stderr)
            return 2
        run = matches[0]
    else:
        candidates = [r for r in runs if r.remaining > 0]
        if not candidates:
            print("Δεν υπάρχει εκτέλεση με αρχεία προς αναίρεση.")
            return 0
        run = candidates[0]
    m = mfmod.read(run.path)
    ops = pending_ops(m)
    if not ops:
        print("Δεν υπάρχει τίποτα προς αναίρεση σε αυτή την εκτέλεση.")
        return 0
    print(f"Εκτέλεση {run.run_id} ({run.started.replace('T', ' ')})")
    print(format_undo_preview(m, ops))
    if not args.yes:
        if not sys.stdin.isatty():
            print("\nΧρειάζεται έγκριση. Τρέξε την εντολή σε τερματικό ή πρόσθεσε --yes. "
                  "Δεν αναιρέθηκε τίποτα.", file=sys.stderr)
            return 1
        try:
            if input("\nΓράψε ΝΑΙ για να επιστρέψουν τα αρχεία: ").strip() != "ΝΑΙ":
                print("Ακυρώθηκε. Δεν αναιρέθηκε τίποτα.")
                return 1
        except (EOFError, KeyboardInterrupt):
            print("\nΑκυρώθηκε. Δεν αναιρέθηκε τίποτα.")
            return 1
    try:
        res = run_undo(run.path, allow_modified=args.include_modified)
    except ValueError as exc:
        print(f"Σφάλμα: {exc}", file=sys.stderr)
        return 2
    for note in res.recovered_notes:
        print(f"Ανάκτηση από προηγούμενη διακοπή: {note}")
    print(f"\nΕπέστρεψαν {res.restored} αρχεία, παραλείφθηκαν {len(res.skipped)}, "
          f"αφαιρέθηκαν {res.removed_dirs} άδειοι φάκελοι που είχε φτιάξει η εκτέλεση.")
    for op, why in res.skipped:
        print(f"  ! {op.src[-1]}: {why}")
    if res.skipped:
        print("Όσα παραλείφθηκαν μπορείς να τα ξαναδοκιμάσεις αργότερα με την ίδια εντολή.")
    return 0 if not res.skipped else 3
