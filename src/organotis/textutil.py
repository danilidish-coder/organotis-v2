"""Κοινά εργαλεία κειμένου (χωρίς εξαρτήσεις), ώστε να μην υπάρχουν κυκλικές εισαγωγές."""
from __future__ import annotations

import re
import unicodedata


def normalize(text: str) -> str:
    """Πεζά, χωρίς τόνους, ς→σ, ώστε «ΔΕΗ», «Δεη» και «δεή» να ταυτίζονται."""
    text = unicodedata.normalize("NFD", text.casefold())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return text.replace("ς", "σ")


def word_pattern(word: str) -> re.Pattern[str]:
    """Ολόκληρη λέξη (όχι κομμάτι άλλης λέξης), μετά την κανονικοποίηση."""
    return re.compile(r"(?<!\w)" + re.escape(normalize(word).strip()) + r"(?!\w)")


def name_text(stem: str) -> str:
    """Το όνομα αρχείου ως κείμενο για αναζήτηση λέξεων (το «_» ως διάστημα)."""
    return stem.replace("_", " ")
