"""Αναφορά σάρωσης σε απλά ελληνικά."""
from __future__ import annotations

from pathlib import Path

from .scanner import Report

CONFIDENCE_LABELS = {"sure": "ΣΙΓΟΥΡΗ", "check": "ΝΑ ΤΗ ΔΕΙΣ"}


def confidence_line(p) -> str:
    label = CONFIDENCE_LABELS.get(p.confidence, "ΝΑ ΤΗ ΔΕΙΣ")
    return f"{label}, επειδή {p.confidence_why}." if p.confidence_why else f"{label}."


KIND_LABELS = {
    "word": "Word", "pdf": "PDF", "excel": "Excel", "image": "Εικόνες",
    "exe": "EXE (δεν ανοίγονται ποτέ)", "other": "Άλλα",
}


def human_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{int(size)} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} B"


def _rel(p: Path, root: Path) -> str:
    try:
        return str(p.relative_to(root))
    except ValueError:
        return str(p)


def format_report(r: Report, max_groups: int = 50) -> str:
    out: list[str] = []
    out.append(f"Σάρωση: {r.root}")
    out.append("(Μόνο ανάγνωση: δεν μετακινήθηκε ούτε άλλαξε τίποτα.)")
    out.append("")
    out.append(f"Σύνολο: {r.total} αρχεία, {human_size(r.total_size)}")
    out.append("")
    out.append("Αρχεία ανά τύπο:")
    for kind, (count, size) in r.by_kind.items():
        out.append(f"  {KIND_LABELS.get(kind, kind):<28} {count:>6}  {human_size(size):>10}")

    out.append("")
    out.append(
        f"Διπλότυπα (ίδιο περιεχόμενο): {len(r.duplicate_groups)} ομάδες, "
        f"{r.duplicate_extra_files} επιπλέον αντίγραφα, "
        f"{human_size(r.duplicate_wasted_bytes)} χώρος"
    )
    for g in r.duplicate_groups[:max_groups]:
        out.append(f"  • {g[0].path.name} ({human_size(g[0].size)}), {len(g)} αντίγραφα:")
        out.extend(f"      {_rel(e.path, r.root)}" for e in g)
    if len(r.duplicate_groups) > max_groups:
        out.append(f"  … και {len(r.duplicate_groups) - max_groups} ακόμη ομάδες.")

    out.append("")
    out.append(
        f"Ίδιο όνομα σε διαφορετικές θέσεις, με διαφορετικό περιεχόμενο: "
        f"{len(r.same_name_groups)} ονόματα"
    )
    for g in r.same_name_groups[:max_groups]:
        out.append(f"  • {g[0].path.name}:")
        out.extend(f"      {_rel(e.path, r.root)} ({human_size(e.size)})" for e in g)
    if len(r.same_name_groups) > max_groups:
        out.append(f"  … και {len(r.same_name_groups) - max_groups} ακόμη ονόματα.")

    if r.skipped_links:
        out.append("")
        out.append(f"Παραλείφθηκαν {r.skipped_links} συντομεύσεις/σύνδεσμοι (δεν ακολουθούνται).")
    if r.problems:
        out.append("")
        out.append(f"Προβλήματα ({len(r.problems)}), τα αρχεία αυτά δεν συμμετέχουν στη σύγκριση:")
        out.extend(f"  ! {_rel(p.path, r.root)}: {p.reason}" for p in r.problems)
    return "\n".join(out)


def format_plan(plan, max_rows: int = 200) -> str:
    """Πρόταση δομής σε απλά ελληνικά. Δεν αλλάζει τίποτα."""
    from .planner import KEEP, MOVE, REVIEW

    root = plan.root
    moves, reviews, keeps = plan.by_action(MOVE), plan.by_action(REVIEW), plan.by_action(KEEP)
    out = [f"Πρόταση δομής για: {root}",
           "(Μόνο πρόταση: δεν μετακινήθηκε ούτε άλλαξε τίποτα. Κάθε γραμμή έχει τον λόγο της.)",
           "",
           f"Προτείνονται {len(moves)} μετακινήσεις, {len(reviews)} αρχεία στον φάκελο ελέγχου "
           f"(διπλότυπα), {len(keeps)} μένουν στη θέση τους."]

    def block(title: str, items: list) -> None:
        if not items:
            return
        out.extend(["", title])
        for p in items[:max_rows]:
            dest = _rel(p.dest, root) if p.dest else "(μένει εκεί που είναι)"
            out.append(f"  • {_rel(p.entry.path, root)}")
            out.append(f"      → {dest}")
            out.append(f"      Λόγος [{p.rule}]: {p.reason}")
            if p.action != KEEP:
                out.append(f"      Βεβαιότητα: {confidence_line(p)}")
        if len(items) > max_rows:
            out.append(f"  … και {len(items) - max_rows} ακόμη.")

    block("Μετακινήσεις:", moves)
    block("Στον φάκελο ελέγχου (διπλότυπα, καμία διαγραφή):", reviews)
    block("Μένουν στη θέση τους:", keeps)
    return "\n".join(out)


def format_preview(model, max_rows: int = 500) -> str:
    """Αριθμημένος πίνακας προεπισκόπησης, με λόγο και βεβαιότητα σε κάθε γραμμή."""
    root = model.root
    sure = sum(1 for r in model.active if r.proposal.confidence == "sure")
    out = [f"Προεπισκόπηση: {len(model.active)} από {len(model.rows)} γραμμές θα εκτελεστούν "
           f"({sure} σίγουρες, {len(model.active) - sure} «να τις δεις»)."]
    for r in model.rows[:max_rows]:
        p = r.proposal
        mark = "[αφαιρέθηκε] " if r.number in model.removed else ""
        where = "ΦΑΚΕΛΟΣ ΕΛΕΓΧΟΥ" if p.action == "review" else "μετακίνηση"
        label = CONFIDENCE_LABELS.get(p.confidence, "ΝΑ ΤΗ ΔΕΙΣ")
        out.append(f"{r.number:>4}. {mark}{_rel(p.entry.path, root)}   [{label}]")
        out.append(f"      → {_rel(p.dest, root)}   ({where})")
        out.append(f"      Λόγος [{p.rule}]: {p.reason}")
        out.append(f"      Βεβαιότητα: {confidence_line(p)}")
    if len(model.rows) > max_rows:
        out.append(f"… και {len(model.rows) - max_rows} ακόμη γραμμές (δεν εμφανίζονται εδώ).")
    return "\n".join(out)


def format_runs(runs) -> str:
    if not runs:
        return "Δεν υπάρχουν καταγεγραμμένες εκτελέσεις."
    out = ["Εκτελέσεις (οι πιο πρόσφατες πρώτα):"]
    for r in runs:
        out.append(f"  {r.run_id}   {r.started.replace('T', ' ')}   {r.moved} αρχεία   {r.label}")
        out.append(f"      φάκελος: {r.root}")
    return "\n".join(out)


def format_undo_preview(m, ops, max_rows: int = 200) -> str:
    out = [f"Θα επιστραφούν {len(ops)} αρχεία στην αρχική τους θέση (φάκελος: {m.root}):"]
    for op in ops[:max_rows]:
        out.append(f"  • {'/'.join(op.dest)}")
        out.append(f"      → {'/'.join(op.src)}")
    if len(ops) > max_rows:
        out.append(f"  … και {len(ops) - max_rows} ακόμη.")
    return "\n".join(out)


def format_rules(rules: list[dict]) -> str:
    from .learning import describe_rule
    if not rules:
        return "Δεν έχει μάθει κανέναν κανόνα ακόμη."
    return "Κανόνες που έμαθε το πρόγραμμα από τις διορθώσεις σου:\n" + "\n".join(
        "  " + describe_rule(r) for r in sorted(rules, key=lambda r: r["id"]))
