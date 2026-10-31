"""Αρχείο ρυθμίσεων ανά υπολογιστή.

Καμία σταθερή διαδρομή στον κώδικα: οι φάκελοι σάρωσης ορίζονται από τον
χρήστη στο αρχείο ρυθμίσεων του συγκεκριμένου υπολογιστή.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

SCHEMA_VERSION = 1
ENV_CONFIG = "ORGANOTIS_CONFIG"


def _is_simple_name(name: object) -> bool:
    return (isinstance(name, str) and bool(name.strip()) and name not in (".", "..")
            and not any(c in name for c in '/\\:*?"<>|'))


class ConfigError(ValueError):
    """Μη έγκυρο αρχείο ρυθμίσεων (το μήνυμα είναι σε απλά ελληνικά)."""


def default_config_path() -> Path:
    """Θέση του αρχείου ρυθμίσεων για τον τρέχοντα υπολογιστή/χρήστη."""
    override = os.environ.get(ENV_CONFIG)
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        base_path = Path(base) if base else Path.home() / "AppData" / "Roaming"
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME")
        base_path = Path(xdg) if xdg else Path.home() / ".config"
    return base_path / "organotis" / "config.json"


def _default_topic_rules() -> list[dict]:
    # Κάθε κανόνας: θέμα + λέξεις-κλειδιά. Η σύγκριση αγνοεί τόνους και κεφαλαία.
    # Δεν υπάρχουν κανόνες για ονόματα προσώπων (αρχή 4).
    return [
        {"topic": "Φορολογικά", "keywords": [
            "εκκαθαριστικο", "φορολογικη δηλωση", "αφμ", "ααδε", "ε1", "ε9", "ενφια", "δου"]},
        {"topic": "Λογαριασμοί", "keywords": [
            "δεη", "ευδαπ", "λογαριασμος", "ρευμα", "οτε", "cosmote", "vodafone", "nova", "κοινοχρηστα"]},
        {"topic": "Τράπεζα", "keywords": [
            "τραπεζα", "iban", "κινηση λογαριασμου", "δανειο", "καταθεση", "πιστωτικη καρτα"]},
        {"topic": "Ασφάλιση", "keywords": [
            "ασφαλιστηριο", "ασφαλιση", "ασφαλιστικη", "εφκα", "ικα", "συνταξη"]},
        {"topic": "Υγεία", "keywords": [
            "εξετασεις", "ιατρικη", "γνωματευση", "εοπυυ", "συνταγη", "νοσοκομειο"]},
        {"topic": "Εκπαίδευση", "keywords": [
            "απολυτηριο", "πτυχιο", "βαθμολογια", "σχολειο", "πανεπιστημιο", "πιστοποιητικο σπουδων"]},
        {"topic": "Συμβόλαια", "keywords": [
            "συμβολαιο", "μισθωτηριο", "συμφωνητικο", "αγοραπωλησια"]},
    ]


@dataclass
class Config:
    # Φάκελοι που σαρώνονται (μόνο ανάγνωση). Κενό μέχρι να τους ορίσει ο χρήστης.
    scan_roots: list[str] = field(default_factory=list)
    # Φάκελος ελέγχου για διπλότυπα/αμφίβολα (σχετικός με τον φάκελο σάρωσης).
    review_folder_name: str = "_Προς_έλεγχο"
    # Φάκελος manifest. Κενό = δίπλα στο αρχείο ρυθμίσεων.
    manifest_dir: str = ""
    # Κατηγορίες αρχείων ανά κατάληξη (πεζά, με τελεία).
    extensions: dict[str, list[str]] = field(default_factory=lambda: {
        "word": [".doc", ".docx"],
        "pdf": [".pdf"],
        "excel": [".xls", ".xlsx"],
        "image": [".jpg", ".jpeg", ".png"],
        "exe": [".exe"],
    })
    # Κανόνες θέματος: λίστα από {"topic": ..., "keywords": [...]}.
    topic_rules: list[dict] = field(default_factory=_default_topic_rules)
    # Ονόματα φακέλων προορισμού (απλά ονόματα, μέσα στον φάκελο σάρωσης).
    documents_folder: str = "Έγγραφα"
    images_folder: str = "Εικόνες"
    programs_folder: str = "Προγράμματα"
    duplicates_subfolder: str = "Διπλότυπα"
    # Κανόνες που έμαθε το πρόγραμμα από τις διορθώσεις σου (βλ. learning.py). Μόνο τοπικά.
    learned_rules: list[dict] = field(default_factory=list)
    schema_version: int = SCHEMA_VERSION

    def validate(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ConfigError(
                f"Η έκδοση ρυθμίσεων {self.schema_version} δεν υποστηρίζεται "
                f"(αναμένεται {SCHEMA_VERSION})."
            )
        if not isinstance(self.scan_roots, list) or not all(
            isinstance(p, str) and p.strip() for p in self.scan_roots
        ):
            raise ConfigError("Το «scan_roots» πρέπει να είναι λίστα με διαδρομές φακέλων.")
        if not _is_simple_name(self.review_folder_name):
            raise ConfigError(
                "Το «review_folder_name» πρέπει να είναι απλό όνομα φακέλου, χωρίς κάθετους."
            )
        for attr in ("documents_folder", "images_folder", "programs_folder", "duplicates_subfolder"):
            if not _is_simple_name(getattr(self, attr)):
                raise ConfigError(f"Το «{attr}» πρέπει να είναι απλό όνομα φακέλου, χωρίς κάθετους.")
        if not isinstance(self.topic_rules, list):
            raise ConfigError("Το «topic_rules» πρέπει να είναι λίστα κανόνων.")
        for rule in self.topic_rules:
            if (not isinstance(rule, dict) or set(rule) != {"topic", "keywords"}
                    or not _is_simple_name(rule["topic"])
                    or not isinstance(rule["keywords"], list) or not rule["keywords"]
                    or not all(isinstance(k, str) and k.strip() for k in rule["keywords"])):
                raise ConfigError(
                    "Κάθε κανόνας θέματος χρειάζεται «topic» (απλό όνομα φακέλου) "
                    "και μη κενή λίστα «keywords»."
                )
        if not isinstance(self.learned_rules, list):
            raise ConfigError("Το «learned_rules» πρέπει να είναι λίστα.")
        from .learning import validate_rule
        ids: set[int] = set()
        for rule in self.learned_rules:
            err = validate_rule(rule, _is_simple_name)
            if err:
                raise ConfigError(err)
            if rule["id"] in ids:
                raise ConfigError(f"Δύο μαθημένοι κανόνες έχουν το ίδιο «id» ({rule['id']}).")
            ids.add(rule["id"])
        if not isinstance(self.manifest_dir, str):
            raise ConfigError("Το «manifest_dir» πρέπει να είναι κείμενο.")
        if not isinstance(self.extensions, dict):
            raise ConfigError("Το «extensions» πρέπει να είναι λεξικό κατηγοριών.")
        seen: dict[str, str] = {}
        for kind, exts in self.extensions.items():
            if not isinstance(exts, list):
                raise ConfigError(f"Η κατηγορία «{kind}» πρέπει να έχει λίστα καταλήξεων.")
            for e in exts:
                if not isinstance(e, str) or not e.startswith(".") or e != e.lower() or len(e) < 2:
                    raise ConfigError(
                        f"Μη έγκυρη κατάληξη «{e}» στην κατηγορία «{kind}» "
                        "(π.χ. «.pdf», με τελεία και πεζά)."
                    )
                if e in seen and seen[e] != kind:
                    raise ConfigError(
                        f"Η κατάληξη «{e}» υπάρχει σε δύο κατηγορίες: «{seen[e]}» και «{kind}»."
                    )
                seen[e] = kind

    def kind_of(self, filename: str) -> str | None:
        """Κατηγορία αρχείου από την κατάληξή του (ή None αν είναι άγνωστη)."""
        ext = os.path.splitext(filename)[1].lower()
        for kind, exts in self.extensions.items():
            if ext in exts:
                return kind
        return None

    def manifest_path(self, config_path: Path) -> Path:
        if self.manifest_dir:
            return Path(self.manifest_dir).expanduser()
        return config_path.parent / "manifests"

    def to_dict(self) -> dict:
        return asdict(self)


def load(path: Path | None = None) -> Config:
    """Φορτώνει τις ρυθμίσεις. Αν το αρχείο δεν υπάρχει, επιστρέφει προεπιλογές."""
    path = path or default_config_path()
    if not path.exists():
        return Config()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Το αρχείο ρυθμίσεων «{path}» δεν είναι έγκυρο JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"Το αρχείο ρυθμίσεων «{path}» πρέπει να περιέχει αντικείμενο JSON.")
    known = set(Config.__dataclass_fields__)
    unknown = sorted(set(raw) - known)
    if unknown:
        raise ConfigError("Άγνωστες ρυθμίσεις: " + ", ".join(unknown))
    cfg = Config(**raw)
    cfg.validate()
    return cfg


def save(cfg: Config, path: Path | None = None) -> Path:
    """Αποθηκεύει ατομικά (προσωρινό αρχείο και μετονομασία)."""
    cfg.validate()
    path = path or default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(cfg.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path
