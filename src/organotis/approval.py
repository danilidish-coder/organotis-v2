"""Προεπισκόπηση και έγκριση (αρχή 1).

Μόνο ένα αντικείμενο `ApprovedPlan` μπορεί να δοθεί στον μεταφορέα, και
φτιάχνεται μόνο από το `PreviewModel.approve()`, αφού ο χρήστης έχει δει
τον πίνακα και έχει αφαιρέσει όσες γραμμές δεν θέλει.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .planner import MOVE, REVIEW, SURE, Plan, Proposal

_TOKEN = object()


@dataclass(frozen=True)
class Row:
    number: int          # αριθμός γραμμής στον πίνακα (1, 2, 3, …)
    proposal: Proposal


class ApprovedPlan:
    """Εγκεκριμένες μετακινήσεις. Δεν φτιάχνεται απευθείας: μόνο μέσω `approve()`."""

    def __init__(self, root: Path, proposals: tuple[Proposal, ...], removed: int, *, _token=None):
        if _token is not _TOKEN:
            raise TypeError("Το ApprovedPlan φτιάχνεται μόνο από την προεπισκόπηση (PreviewModel.approve).")
        self.root = root
        self.proposals = proposals
        self.removed = removed


class PreviewModel:
    """Η κατάσταση του πίνακα: κάθε γραμμή είναι «σε αναμονή», «εγκεκριμένη» ή «αφαιρεμένη».

    Δύο τρόποι έγκρισης:
    - `approve()`: όλες οι γραμμές που δεν αφαιρέθηκαν (γραμμή εντολών, v0.11).
    - `approve_selected()`: μόνο όσες εγκρίθηκαν ρητά (παράθυρο): ένα-ένα ή «όλες οι σίγουρες».
    """

    def __init__(self, plan: Plan):
        self.root = plan.root
        self.rows: list[Row] = [
            Row(i, p) for i, p in enumerate(
                (p for p in plan.proposals if p.action in (MOVE, REVIEW)), start=1)
        ]
        self.removed: set[int] = set()
        self.approved: set[int] = set()

    def _check(self, numbers) -> list[int]:
        nums = list(numbers)
        valid = {r.number for r in self.rows}
        bad = [n for n in nums if n not in valid]
        if bad:
            raise ValueError("Δεν υπάρχουν οι γραμμές: " + ", ".join(map(str, bad)))
        return nums

    def remove(self, numbers) -> None:
        nums = self._check(numbers)
        self.removed.update(nums)
        self.approved.difference_update(nums)

    def restore(self, numbers) -> None:
        """Η γραμμή ξαναμπαίνει «σε αναμονή» (δεν εγκρίνεται αυτόματα)."""
        self.removed.difference_update(self._check(numbers))

    def approve_numbers(self, numbers) -> None:
        nums = self._check(numbers)
        self.removed.difference_update(nums)
        self.approved.update(nums)

    def unapprove(self, numbers) -> None:
        self.approved.difference_update(self._check(numbers))

    def approve_all_sure(self) -> int:
        """Εγκρίνει όλες τις «σίγουρες» γραμμές που δεν έχουν αφαιρεθεί. Επιστρέφει πόσες."""
        nums = [r.number for r in self.sure_rows if r.number not in self.removed]
        self.approved.update(nums)
        return len(nums)

    def status(self, number: int) -> str:
        if number in self.removed:
            return "removed"
        return "approved" if number in self.approved else "pending"

    @property
    def sure_rows(self) -> list[Row]:
        return [r for r in self.rows if r.proposal.confidence == SURE]

    @property
    def check_rows(self) -> list[Row]:
        return [r for r in self.rows if r.proposal.confidence != SURE]

    @property
    def active(self) -> list[Row]:
        return [r for r in self.rows if r.number not in self.removed]

    @property
    def approved_rows(self) -> list[Row]:
        return [r for r in self.rows if r.number in self.approved and r.number not in self.removed]

    def approve(self) -> ApprovedPlan:
        """Όλες οι γραμμές που δεν αφαιρέθηκαν (χρήση από τη γραμμή εντολών)."""
        return self._finish(self.active)

    def approve_selected(self) -> ApprovedPlan:
        """Μόνο όσες εγκρίθηκαν ρητά (χρήση από το παράθυρο)."""
        return self._finish(self.approved_rows)

    def _finish(self, rows: list[Row]) -> ApprovedPlan:
        if not rows:
            raise ValueError("Δεν έμεινε καμία γραμμή για μετακίνηση.")
        return ApprovedPlan(self.root, tuple(r.proposal for r in rows),
                            len(self.rows) - len(rows), _token=_TOKEN)

    # Για να μη χάνεται η κατάσταση όταν ξαναχτίζεται η πρόταση (π.χ. μετά από νέο κανόνα).
    def export_state(self) -> dict[Path, str]:
        return {r.proposal.entry.path: self.status(r.number) for r in self.rows}

    def import_state(self, state: dict[Path, str]) -> None:
        for r in self.rows:
            st = state.get(r.proposal.entry.path)
            if st == "removed":
                self.removed.add(r.number)
            elif st == "approved":
                self.approved.add(r.number)


def parse_numbers(text: str) -> list[int]:
    """«1, 3, 5-7» → [1, 3, 5, 6, 7]. Σηκώνει ValueError με ελληνικό μήνυμα."""
    out: list[int] = []
    for part in text.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            if "-" in part:
                a, b = part.split("-", 1)
                lo, hi = int(a), int(b)
                if lo > hi:
                    raise ValueError
                out.extend(range(lo, hi + 1))
            else:
                out.append(int(part))
        except ValueError:
            raise ValueError(f"Δεν κατάλαβα το «{part}». Γράψε αριθμούς όπως 1, 3, 5-7.") from None
    return out
