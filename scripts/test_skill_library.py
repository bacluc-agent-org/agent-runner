import json
import os
import subprocess
import sys
from pathlib import Path

import skill_library


def test_add_retrieve_roundtrip(tmp_path):
    lib = skill_library.SkillLibrary(tmp_path)
    lib.add_skill("s1", "desc one", "hello world content", ["kw1"])
    res = lib.retrieve("kw1")
    assert len(res) == 1
    assert res[0][0] == "s1"
    assert res[0][1] == "hello world content"
    assert res[0][2] > 0


def test_persistence_across_instances(tmp_path):
    a = skill_library.SkillLibrary(tmp_path)
    a.add_skill("s1", "desc one", "persisted content", ["kw1"])
    b = skill_library.SkillLibrary(tmp_path)
    assert b.list_skills() == [{"name": "s1", "description": "desc one"}]
    res = b.retrieve("kw1")
    assert res[0][1] == "persisted content"


def test_keyword_ranking_beats_description_name(tmp_path):
    lib = skill_library.SkillLibrary(tmp_path)
    lib.add_skill("nameonly", "nothing here", "no match here", [])
    # description contains token
    lib.add_skill("descskill", "token in description", "no match here", [])
    # keyword contains token
    lib.add_skill("kwskill", "nothing here", "no match here", ["token"])
    res = lib.retrieve("token")
    names = [r[0] for r in res]
    assert names[0] == "kwskill"
    # scores: keyword 3, description 2, name 1
    scores = {r[0]: r[2] for r in res}
    assert scores["kwskill"] == 3
    assert scores["descskill"] == 2
    # nameonly: name doesn't contain 'token' literally? name 'nameonly' no. Actually no hit -> excluded
    assert "nameonly" not in scores


def test_upsert_duplicate_name(tmp_path):
    lib = skill_library.SkillLibrary(tmp_path)
    lib.add_skill("dup", "old desc", "old content", ["oldkw"])
    old_file = (tmp_path / "dup.md")
    # change sanitized filename by using name with space? same name keeps same file.
    # To test old file deletion, use name change that sanitizes differently? name same -> same file.
    # Instead test content replaced and index length unchanged.
    lib.add_skill("dup", "new desc", "new content", ["newkw"])
    assert len(lib.list_skills()) == 1
    res = lib.retrieve("newkw")
    assert res[0][1] == "new content"
    # old content gone
    assert "old content" not in (tmp_path / "dup.md").read_text()


def test_upsert_old_file_deleted_when_filename_changes(tmp_path):
    lib = skill_library.SkillLibrary(tmp_path)
    lib.add_skill("myskill", "desc", "content1", ["kw"])
    # simulate stale index pointing at a different content file
    (tmp_path / "stale.md").write_text("stale", encoding="utf-8")
    idx = tmp_path / "index.json"
    import json
    records = json.loads(idx.read_text())
    records[0]["file"] = "stale.md"
    idx.write_text(json.dumps(records), encoding="utf-8")
    lib.add_skill("myskill", "desc2", "content2", ["kw2"])
    assert not (tmp_path / "stale.md").exists()
    assert (tmp_path / "myskill.md").exists()


def test_empty_and_corrupt_index(tmp_path):
    idx = tmp_path / "index.json"
    tmp_path.mkdir(parents=True, exist_ok=True)
    # empty file
    idx.write_text("", encoding="utf-8")
    lib = skill_library.SkillLibrary(tmp_path)
    assert lib.list_skills() == []
    assert lib.retrieve("anything") == []
    # corrupt
    idx.write_text("{bad", encoding="utf-8")
    assert lib.list_skills() == []
    assert lib.retrieve("anything") == []
    # non-list
    idx.write_text(json.dumps({"a": 1}), encoding="utf-8")
    assert lib.list_skills() == []
    assert lib.retrieve("anything") == []
    # valid empty list
    idx.write_text("[]", encoding="utf-8")
    assert lib.list_skills() == []
    assert lib.retrieve("anything") == []


def test_missing_index(tmp_path):
    lib = skill_library.SkillLibrary(tmp_path)
    assert lib.list_skills() == []
    assert lib.retrieve("q") == []


def test_top_k_and_score_filter(tmp_path):
    lib = skill_library.SkillLibrary(tmp_path)
    lib.add_skill("a", "d", "content a", ["ka"])
    lib.add_skill("b", "d", "content b", ["kb"])
    assert lib.retrieve("ka", top_k=0) == []
    assert lib.retrieve("ka", top_k=-1) == []
    res = lib.retrieve("ka", top_k=1)
    assert len(res) == 1
    res = lib.retrieve("ka", top_k=100)
    assert len(res) == 1
    # no match
    assert lib.retrieve("zzzzz") == []
    # empty query
    assert lib.retrieve("") == []
    assert lib.retrieve("   ") == []


def test_tokenization(tmp_path):
    lib = skill_library.SkillLibrary(tmp_path)
    lib.add_skill("my skill", "desc", "content", ["mykw"])
    # punctuation splits, case-insensitive
    res = lib.retrieve("MY,SKILL!")
    # tokens: my, skill. 'my' in name -> hit; 'skill' in name -> hit
    assert res and res[0][2] > 0
    # duplicate tokens count multiple
    lib2 = skill_library.SkillLibrary(tmp_path)
    # score for "my my" should be double single for name hit
    r1 = lib2.retrieve("my")
    r2 = lib2.retrieve("my my")
    assert r2[0][2] == 2 * r1[0][2]


def test_missing_content_file(tmp_path):
    lib = skill_library.SkillLibrary(tmp_path)
    lib.add_skill("s", "desc with token", "content", ["kw"])
    # delete content file
    (tmp_path / "s.md").unlink()
    res = lib.retrieve("token")
    # metadata still scores (description hit)
    assert res and res[0][0] == "s"
    assert res[0][1] == ""


def test_self_check():
    skill_library._self_check()


def test_cli_store_retrieve_list(tmp_path):
    # store with --content
    r = subprocess.run(
        [sys.executable, str(Path(__file__).parent / "skill_library.py"),
         "store", "--dir", str(tmp_path), "--name", "cli", "--description", "cli desc",
         "--keywords", "a, b, ,c", "--content", "cli content"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    # store with --content-file
    cf = tmp_path / "cf.md"
    cf.write_text("file content")
    r = subprocess.run(
        [sys.executable, str(Path(__file__).parent / "skill_library.py"),
         "store", "--dir", str(tmp_path), "--name", "cli2", "--description", "d2",
         "--keywords", "x", "--content-file", str(cf)],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    # list
    r = subprocess.run(
        [sys.executable, str(Path(__file__).parent / "skill_library.py"),
         "list", "--dir", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert r.returncode == 0
    assert "cli: cli desc" in r.stdout
    assert "cli2: d2" in r.stdout
    # retrieve
    r = subprocess.run(
        [sys.executable, str(Path(__file__).parent / "skill_library.py"),
         "retrieve", "--dir", str(tmp_path), "--query", "a", "--top-k", "5"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0
    assert "cli (score=" in r.stdout
    assert "cli content" in r.stdout
    # empty list case
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    r = subprocess.run(
        [sys.executable, str(Path(__file__).parent / "skill_library.py"),
         "list", "--dir", str(empty_dir)],
        capture_output=True, text=True,
    )
    assert r.returncode == 0
    assert "(empty)" in r.stdout


def test_cli_demo_twice(tmp_path):
    demo_dir = tmp_path / "demo"
    script = Path(__file__).parent / "skill_library.py"
    r1 = subprocess.run(
        [sys.executable, str(script), "demo", "--dir", str(demo_dir)],
        capture_output=True, text=True,
    )
    assert r1.returncode == 0, r1.stderr
    assert "Short markdown body for demo." in r1.stdout
    # index length 1
    idx = json.loads((demo_dir / "index.json").read_text())
    assert len(idx) == 1
    r2 = subprocess.run(
        [sys.executable, str(script), "demo", "--dir", str(demo_dir)],
        capture_output=True, text=True,
    )
    assert r2.returncode == 0, r2.stderr
    idx2 = json.loads((demo_dir / "index.json").read_text())
    assert len(idx2) == 1
    assert "Short markdown body for demo." in r2.stdout


def test_dir_precedence_env(tmp_path, monkeypatch):
    d = tmp_path / "envdir"
    d.mkdir()
    monkeypatch.setenv("SKILL_LIBRARY_DIR", str(d))
    lib = skill_library.SkillLibrary(skill_library._resolve_dir(None))
    assert lib.library_dir == d
    # --dir overrides
    assert skill_library._resolve_dir(str(tmp_path / "x")) == Path(tmp_path / "x")


def test_sanitize_filename():
    assert skill_library._sanitize_filename("a b!c") == "a_b_c.md"
