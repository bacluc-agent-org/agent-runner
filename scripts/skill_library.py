"""On-disk Voyager-style skill library storing indexed markdown snippets.

Stdlib only. Persists skills (name, description, keywords, content) to a
library directory containing ``index.json`` plus one ``.md`` file per skill,
and retrieves them by weighted keyword ranking.

Exit codes: 0 success, 1 failure.
"""
import argparse
import datetime
import json
import os
import re
import sys
import tempfile
from pathlib import Path


def _sanitize_filename(name: str) -> str:
    return re.sub(r"[^0-9A-Za-z]", "_", name) + ".md"


def _resolve_dir(cli_dir, default=None):
    if cli_dir:
        return Path(cli_dir)
    env = os.environ.get("SKILL_LIBRARY_DIR")
    if env:
        return Path(env)
    if default is not None:
        return Path(default)
    return Path(__file__).resolve().parent.parent / "skills"


class SkillLibrary:
    def __init__(self, library_dir):
        self.library_dir = Path(library_dir)

    def _index_path(self):
        return self.library_dir / "index.json"

    def _read_index(self):
        path = self._index_path()
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, FileNotFoundError):
            return []
        try:
            data = json.loads(text)
        except (ValueError, json.JSONDecodeError):
            return []
        if not isinstance(data, list):
            return []
        return data

    def _write_index(self, records):
        self.library_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.library_dir))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(records, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self._index_path())
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def add_skill(self, name, description, content, keywords):
        self.library_dir.mkdir(parents=True, exist_ok=True)
        new_file = _sanitize_filename(name)
        (self.library_dir / new_file).write_text(content, encoding="utf-8")
        records = self._read_index()
        old_file = None
        for r in records:
            if r.get("name") == name:
                old_file = r.get("file")
                break
        records = [r for r in records if r.get("name") != name]
        if old_file and old_file != new_file:
            try:
                (self.library_dir / old_file).unlink()
            except FileNotFoundError:
                pass
        record = {
            "name": name,
            "description": description,
            "keywords": list(keywords) if keywords is not None else [],
            "file": new_file,
            "created": datetime.datetime.now().isoformat(),
        }
        records.append(record)
        self._write_index(records)

    def retrieve(self, query, top_k=5):
        if not top_k or top_k <= 0:
            return []
        tokens = [t for t in re.split(r"[^0-9a-zA-Z]+", query.lower()) if t]
        if not tokens:
            return []
        records = self._read_index()
        results = []
        for r in records:
            name = r.get("name", "")
            description = r.get("description", "")
            keywords = r.get("keywords", []) or []
            keywords_str = " ".join(str(k) for k in keywords).lower()
            desc_lower = str(description).lower()
            name_lower = str(name).lower()
            file_name = r.get("file", "")
            content = ""
            if file_name:
                try:
                    content = (self.library_dir / file_name).read_text(encoding="utf-8")
                except (OSError, FileNotFoundError):
                    content = ""
            content_lower = content.lower()
            score = 0
            for tok in tokens:
                if tok in keywords_str:
                    score += 3
                if tok in desc_lower:
                    score += 2
                if tok in name_lower:
                    score += 1
                if tok in content_lower:
                    score += 1
            if score > 0:
                results.append((name, content, score))
        results.sort(key=lambda x: x[2], reverse=True)
        return results[:top_k]

    def list_skills(self):
        records = self._read_index()
        return [{"name": r.get("name"), "description": r.get("description")} for r in records]


def _self_check():
    with tempfile.TemporaryDirectory() as td:
        lib = SkillLibrary(td)
        lib.add_skill("alpha", "alpha desc", "alpha content", ["alpha"])
        lib.add_skill("beta", "beta desc", "beta content", ["beta"])
        res = lib.retrieve("alpha")
        assert res and res[0][0] == "alpha", "correct skill should rank first"
        assert res[0][1] == "alpha content", "content should round-trip exactly"
        # missing/corrupt handling
        idx = Path(td) / "index.json"
        idx.write_text("{bad json", encoding="utf-8")
        assert lib.list_skills() == []
        assert lib.retrieve("alpha") == []
        idx.unlink()
        assert lib.list_skills() == []
        assert lib.retrieve("alpha") == []


def main(argv=None):
    parser = argparse.ArgumentParser(description="Voyager-style skill library")
    sub = parser.add_subparsers(dest="command", required=True)

    p_store = sub.add_parser("store")
    p_store.add_argument("--dir", default=None)
    p_store.add_argument("--name", required=True)
    p_store.add_argument("--description", required=True)
    p_store.add_argument("--keywords", required=True)
    group = p_store.add_mutually_exclusive_group(required=True)
    group.add_argument("--content")
    group.add_argument("--content-file")

    p_ret = sub.add_parser("retrieve")
    p_ret.add_argument("--dir", default=None)
    p_ret.add_argument("--query", required=True)
    p_ret.add_argument("--top-k", type=int, default=5, dest="top_k")

    p_list = sub.add_parser("list")
    p_list.add_argument("--dir", default=None)

    p_demo = sub.add_parser("demo")
    p_demo.add_argument("--dir", default=None)

    args = parser.parse_args(argv)

    if args.command == "store":
        lib_dir = _resolve_dir(args.dir)
        keywords = [k.strip() for k in args.keywords.split(",")]
        keywords = [k for k in keywords if k]
        if args.content_file:
            content = Path(args.content_file).read_text(encoding="utf-8")
        else:
            content = args.content
        lib = SkillLibrary(lib_dir)
        lib.add_skill(args.name, args.description, content, keywords)
        return 0

    if args.command == "retrieve":
        lib_dir = _resolve_dir(args.dir)
        lib = SkillLibrary(lib_dir)
        results = lib.retrieve(args.query, args.top_k)
        for name, content, score in results:
            print(f"{name} (score={score}):")
            print(content, end="" if content.endswith("\n") else "\n")
        return 0

    if args.command == "list":
        lib_dir = _resolve_dir(args.dir)
        lib = SkillLibrary(lib_dir)
        skills = lib.list_skills()
        if not skills:
            print("(empty)")
        else:
            for s in skills:
                print(f"{s['name']}: {s['description']}")
        return 0

    if args.command == "demo":
        repo_root = Path(__file__).resolve().parent.parent
        default = repo_root / ".skill-library-demo"
        lib_dir = _resolve_dir(args.dir, default=default)
        lib = SkillLibrary(lib_dir)
        existing_names = [s["name"] for s in lib.list_skills()]
        if "parse_github_issue" not in existing_names:
            body = "# parse_github_issue\n\nShort markdown body for demo.\n"
            lib.add_skill(
                "parse_github_issue",
                "Parse a GitHub issue",
                body,
                ["github", "issue", "parse"],
            )
        lib2 = SkillLibrary(lib_dir)
        results = lib2.retrieve("parse github issue")
        names = [r[0] for r in results]
        if "parse_github_issue" not in names:
            print("sample skill not in top results", file=sys.stderr)
            return 1
        for name, content, score in results:
            print(content, end="" if content.endswith("\n") else "\n")
        return 0

    return 1


if __name__ == "__main__":
    _self_check()
    raise SystemExit(main())
