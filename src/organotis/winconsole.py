"""Το EXE των Windows είναι «παραθύρου» (χωρίς μαύρη κονσόλα όταν το ανοίγεις με διπλό κλικ).

Όταν όμως το τρέχεις από τερματικό με εντολή (π.χ. `organotis.exe undo`), πρέπει να
φαίνεται η έξοδος. Εδώ συνδεόμαστε στην κονσόλα που μας άνοιξε, αν υπάρχει.
Σε άλλα συστήματα, ή όταν υπάρχει ήδη έξοδος (π.χ. ανακατεύθυνση σε αρχείο), δεν κάνει τίποτα.
"""
from __future__ import annotations

import sys


def attach_if_needed() -> bool:
    """Επιστρέφει True αν συνδέθηκε σε κονσόλα."""
    if sys.platform != "win32" or len(sys.argv) < 2:
        return False
    if sys.stdout is not None and sys.stderr is not None:
        return False  # υπάρχει ήδη έξοδος
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        if not kernel32.AttachConsole(-1):  # -1: η κονσόλα της διεργασίας-γονέα
            return False
        kernel32.SetConsoleOutputCP(65001)  # UTF-8, για να φαίνονται σωστά τα ελληνικά
        sys.stdout = open("CONOUT$", "w", encoding="utf-8", buffering=1)
        sys.stderr = open("CONOUT$", "w", encoding="utf-8", buffering=1)
        try:
            sys.stdin = open("CONIN$", "r", encoding="utf-8")
        except OSError:
            pass
        return True
    except Exception:
        return False
