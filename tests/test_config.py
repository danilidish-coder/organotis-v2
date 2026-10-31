import json
import re
from pathlib import Path

import pytest

from organotis import __version__, config as cfgmod
from organotis.cli import main


def test_version():
    assert __version__ == "0.12.0"


def test_missing_file_gives_defaults(tmp_path):
    cfg = cfgmod.load(tmp_path / "nope.json")
    assert cfg.scan_roots == []
    assert cfg.kind_of("a.PDF") == "pdf"


def test_roundtrip_greek_paths(tmp_path):
    p = tmp_path / "sub" / "config.json"
    cfg = cfgmod.Config(scan_roots=[str(tmp_path / "Έγγραφα")])
    cfgmod.save(cfg, p)
    assert cfgmod.load(p) == cfg
    assert "Έγγραφα" in p.read_text(encoding="utf-8")  # όχι \u escapes


def test_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv(cfgmod.ENV_CONFIG, str(tmp_path / "x.json"))
    assert cfgmod.default_config_path() == tmp_path / "x.json"


def test_manifest_dir_default_next_to_config(tmp_path):
    cfg = cfgmod.Config()
    assert cfg.manifest_path(tmp_path / "config.json") == tmp_path / "manifests"


@pytest.mark.parametrize("bad", [
    {"scan_roots": "C:/x"},
    {"scan_roots": [""]},
    {"review_folder_name": "a/b"},
    {"review_folder_name": ".."},
    {"extensions": {"pdf": ["pdf"]}},
    {"extensions": {"pdf": [".PDF"]}},
    {"extensions": {"a": [".x"], "b": [".x"]}},
    {"schema_version": 99},
])
def test_invalid_config_rejected(tmp_path, bad):
    p = tmp_path / "c.json"
    p.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(cfgmod.ConfigError):
        cfgmod.load(p)


def test_unknown_key_and_bad_json(tmp_path):
    p = tmp_path / "c.json"
    p.write_text('{"foo": 1}', encoding="utf-8")
    with pytest.raises(cfgmod.ConfigError, match="Άγνωστες"):
        cfgmod.load(p)
    p.write_text("{oops", encoding="utf-8")
    with pytest.raises(cfgmod.ConfigError, match="JSON"):
        cfgmod.load(p)


def test_exe_is_classified_by_name_only():
    cfg = cfgmod.Config()
    assert cfg.kind_of("setup.EXE") == "exe"
    assert cfg.kind_of("readme") is None


def test_cli_init_does_not_overwrite(monkeypatch, tmp_path, capsys):
    p = tmp_path / "c.json"
    monkeypatch.setenv(cfgmod.ENV_CONFIG, str(p))
    assert main(["config", "init"]) == 0
    p.write_text(json.dumps({"scan_roots": ["keep"]}), encoding="utf-8")
    assert main(["config", "init"]) == 1
    assert json.loads(p.read_text(encoding="utf-8")) == {"scan_roots": ["keep"]}


def test_cli_bad_config_exit_code(monkeypatch, tmp_path):
    p = tmp_path / "c.json"
    p.write_text("{oops", encoding="utf-8")
    monkeypatch.setenv(cfgmod.ENV_CONFIG, str(p))
    assert main(["config", "show"]) == 2


def test_no_hardcoded_paths_in_source():
    """Αρχή: καμία σταθερή διαδρομή στον κώδικα."""
    src = Path(cfgmod.__file__).parent
    pattern = re.compile(r"""["'](?:[A-Za-z]:[\\/]|/(?:home|Users|mnt)/)""")
    for f in src.glob("*.py"):
        assert not pattern.search(f.read_text(encoding="utf-8")), f
