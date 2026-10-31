import os
import stat
import sys
from pathlib import Path

import pytest

from organotis.cli import main
from organotis import config as cfgmod
from organotis.config import Config
from organotis.formatting import format_report
from organotis.scanner import build_report, scan


def w(p: Path, data: bytes = b"x") -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def snapshot(root: Path):
    return sorted(
        (str(p.relative_to(root)), p.stat().st_size, p.stat().st_mtime_ns,
         p.read_bytes() if p.is_file() else None)
        for p in root.rglob("*")
    )


def report(root):
    return build_report(scan(root, Config()))


def test_counts_by_kind(tmp_path):
    w(tmp_path / "a.docx"); w(tmp_path / "b.PDF", b"yy"); w(tmp_path / "c.xlsx", b"zzz")
    w(tmp_path / "d.jpg", b"4444"); w(tmp_path / "setup.exe", b"MZ"); w(tmp_path / "notes.txt", b"n")
    r = report(tmp_path)
    assert r.total == 6
    assert {k: v[0] for k, v in r.by_kind.items()} == {
        "word": 1, "pdf": 1, "excel": 1, "image": 1, "exe": 1, "other": 1}


def test_duplicates_by_content_not_name(tmp_path):
    w(tmp_path / "a" / "x.pdf", b"same-content")
    w(tmp_path / "b" / "renamed.pdf", b"same-content")
    w(tmp_path / "c" / "other.pdf", b"different!!!")
    r = report(tmp_path)
    assert len(r.duplicate_groups) == 1
    assert {e.path.name for e in r.duplicate_groups[0]} == {"x.pdf", "renamed.pdf"}
    assert r.duplicate_extra_files == 1
    assert r.duplicate_wasted_bytes == len(b"same-content")


def test_same_size_different_content_is_not_duplicate(tmp_path):
    w(tmp_path / "a.bin", b"aaaa"); w(tmp_path / "b.bin", b"bbbb")
    assert report(tmp_path).duplicate_groups == []


def test_same_size_differs_only_after_first_mb(tmp_path):
    big = b"a" * (1024 * 1024 + 10)
    w(tmp_path / "a.bin", big); w(tmp_path / "b.bin", big[:-1] + b"b")
    assert report(tmp_path).duplicate_groups == []


def test_empty_files_not_duplicates(tmp_path):
    w(tmp_path / "a.txt", b""); w(tmp_path / "b.txt", b"")
    assert report(tmp_path).duplicate_groups == []


def test_same_name_different_content(tmp_path):
    w(tmp_path / "2023" / "Report.docx", b"one")
    w(tmp_path / "2024" / "report.docx", b"two!")
    r = report(tmp_path)
    assert r.duplicate_groups == []
    assert len(r.same_name_groups) == 1
    assert len(r.same_name_groups[0]) == 2


def test_same_name_same_content_listed_only_as_duplicate(tmp_path):
    w(tmp_path / "a" / "f.pdf", b"same"); w(tmp_path / "b" / "f.pdf", b"same")
    r = report(tmp_path)
    assert len(r.duplicate_groups) == 1 and r.same_name_groups == []


def test_same_name_in_same_folder_impossible_but_two_dirs_needed(tmp_path):
    w(tmp_path / "a" / "f.pdf", b"1"); w(tmp_path / "a" / "g.pdf", b"2")
    assert report(tmp_path).same_name_groups == []


def test_scan_changes_nothing(tmp_path):
    w(tmp_path / "a" / "x.pdf", b"dup"); w(tmp_path / "b" / "x.pdf", b"dup")
    w(tmp_path / "c" / "setup.exe", b"MZ")
    before = snapshot(tmp_path)
    text = format_report(report(tmp_path))
    assert snapshot(tmp_path) == before
    assert "Μόνο ανάγνωση" in text


def test_exe_is_never_executed(tmp_path):
    marker = tmp_path / "ran.txt"
    script = w(tmp_path / "evil.exe", f"#!/bin/sh\ntouch {marker}\n".encode())
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    report(tmp_path)
    assert not marker.exists()
    assert report(tmp_path).by_kind["exe"][0] == 1


def test_exe_inside_folder_classified_by_name_only(tmp_path):
    w(tmp_path / "Programs" / "tool" / "tool.exe", b"MZ")
    r = report(tmp_path)
    assert r.by_kind == {"exe": (1, 2)}


def test_review_folder_is_skipped(tmp_path):
    w(tmp_path / "a.pdf", b"dup"); w(tmp_path / "_Προς_έλεγχο" / "a.pdf", b"dup")
    r = report(tmp_path)
    assert r.total == 1 and r.duplicate_groups == []


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks χρειάζονται δικαιώματα")
def test_symlinks_not_followed(tmp_path):
    root = tmp_path / "root"
    w(root / "a.pdf", b"x")
    outside = tmp_path / "outside"; w(outside / "secret.pdf", b"s")
    (root / "loop").symlink_to(root, target_is_directory=True)
    (root / "out").symlink_to(outside, target_is_directory=True)
    (root / "link.pdf").symlink_to(root / "a.pdf")
    r = report(root)
    assert r.total == 1
    assert r.skipped_links == 3  # 2 σύνδεσμοι φακέλων + 1 σύνδεσμος αρχείου


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0,
                    reason="το chmod 000 δεν εμποδίζει τον root / διαφέρει στα Windows")
def test_locked_file_is_reported_not_crash(tmp_path):
    w(tmp_path / "a.pdf", b"samesame")
    locked = w(tmp_path / "b.pdf", b"samesame")
    locked.chmod(0)
    try:
        r = report(tmp_path)
    finally:
        locked.chmod(0o644)
    assert r.duplicate_groups == []
    assert any(p.path == locked for p in r.problems)


def test_locked_file_via_unreadable_open(tmp_path, monkeypatch):
    """Προσομοίωση κλειδωμένου αρχείου (ισχύει και ως root και στα Windows)."""
    a = w(tmp_path / "a.pdf", b"samesame"); b = w(tmp_path / "b.pdf", b"samesame")
    w(tmp_path / "c.pdf", b"samesame")
    import builtins
    real_open = builtins.open

    def fake_open(file, *args, **kw):
        if Path(file) == b:
            raise PermissionError(13, "Permission denied", str(file))
        return real_open(file, *args, **kw)

    monkeypatch.setattr(builtins, "open", fake_open)
    r = report(tmp_path)
    assert [p.path for p in r.problems] == [b]
    assert len(r.duplicate_groups) == 1
    assert {e.path.name for e in r.duplicate_groups[0]} == {"a.pdf", "c.pdf"}
    assert "κλειδωμένο" in format_report(r)


def test_missing_root_reported(tmp_path):
    r = report(tmp_path / "nope")
    assert r.total == 0 and len(r.problems) == 1


def test_greek_names(tmp_path):
    w(tmp_path / "Φορολογικά" / "Δήλωση 2023.pdf", b"abc")
    w(tmp_path / "Αντίγραφο" / "Δήλωση.pdf", b"abc")
    r = report(tmp_path)
    assert len(r.duplicate_groups) == 1
    assert "Δήλωση 2023.pdf" in format_report(r)


def test_cli_scan(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv(cfgmod.ENV_CONFIG, str(tmp_path / "cfg" / "c.json"))
    data = tmp_path / "data"; w(data / "a.pdf", b"1"); w(data / "b" / "a.pdf", b"1")
    assert main(["scan", str(data)]) == 0
    out = capsys.readouterr().out
    assert "1 ομάδες" in out and "Σύνολο: 2 αρχεία" in out


def test_cli_scan_uses_config_roots_and_requires_some(monkeypatch, tmp_path, capsys):
    cfgp = tmp_path / "c.json"
    monkeypatch.setenv(cfgmod.ENV_CONFIG, str(cfgp))
    assert main(["scan"]) == 2
    data = tmp_path / "data"; w(data / "a.pdf", b"1")
    cfgmod.save(Config(scan_roots=[str(data)]), cfgp)
    assert main(["scan"]) == 0
    assert "Σύνολο: 1 αρχεία" in capsys.readouterr().out
