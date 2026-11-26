import argparse
import json
import re
import subprocess
from pathlib import Path


NO_CODE_CHANGE = "AGENT_RESULT: NO_CODE_CHANGE"
OK_VERDICT = "VERDICT: OK"


def repository_roots(workspace):
    workspace = Path(workspace).resolve()
    candidates = [workspace]
    for path in workspace.rglob(".git"):
        candidates.append(path.parent)
    roots = set()
    for candidate in candidates:
        result = subprocess.run(
            ["git", "-C", str(candidate), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            roots.add(str(Path(result.stdout.strip()).resolve()))
    return sorted(roots)


def nested_repository_paths(root):
    result = subprocess.run(
        ["git", "-C", root, "ls-files", "-s"],
        check=True,
        capture_output=True,
        text=True,
    )
    paths = {
        str(Path(root, line.split("\t", 1)[1]).resolve())
        for line in result.stdout.splitlines()
        if line.startswith("160000 ") and "\t" in line
    }
    paths.update(
        candidate
        for candidate in repository_roots(root)
        if candidate != str(Path(root).resolve())
    )
    return sorted(paths)


def relevant_status(root):
    nested_paths = nested_repository_paths(root)
    result = subprocess.run(
        ["git", "-C", root, "status", "--porcelain=v1", "--untracked-files=all"],
        check=True,
        capture_output=True,
        text=True,
    )
    status = []
    for line in result.stdout.splitlines():
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        absolute_path = Path(root, path).resolve()
        if any(
            absolute_path == Path(nested) or Path(nested) in absolute_path.parents
            for nested in nested_paths
        ):
            continue
        if Path(path) in {Path(".opencode/package.json"), Path(".opencode/package-lock.json")}:
            # these two files are rewritten at runtime; other .opencode/ files count as delivery
            continue
        status.append(line)
    return "\n".join(status)


def repository_state(root):
    head = subprocess.run(
        ["git", "-C", root, "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return {"head": head, "status": relevant_status(root)}


def snapshot(workspace):
    return {root: repository_state(root) for root in repository_roots(workspace)}


def has_delivery(baseline, current):
    for root, state in current.items():
        if root not in baseline:
            return True
        if state["head"] != baseline[root]["head"] or state["status"] != baseline[root]["status"]:
            return True
    return False


def _normalize_lines(text):
    return [re.sub(r"\x1b\[[0-9;]*m", "", line).strip() for line in text.splitlines()]


def check(workspace, output, baseline_path, verdict_path=None):
    baseline = json.loads(Path(baseline_path).read_text())
    current = snapshot(workspace)
    if verdict_path is not None and OK_VERDICT not in _normalize_lines(Path(verdict_path).read_text()):
        print("output audit did not return VERDICT: OK; failing closed")
        return 1
    if has_delivery(baseline, current):
        print("observable delivery detected")
        return 0
    if NO_CODE_CHANGE in _normalize_lines(Path(output).read_text()):
        print("explicit no-code-change result")
        return 2
    print("incomplete coordinator run: no observable delivery or exact no-code-change result")
    return 1


def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--snapshot", action="store_true")
    mode.add_argument("--check", action="store_true")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--output")
    parser.add_argument("--verdict")
    args = parser.parse_args()
    if args.snapshot:
        Path(args.baseline).write_text(json.dumps(snapshot(args.workspace), sort_keys=True))
        return 0
    if not args.output:
        parser.error("--output is required with --check")
    return check(args.workspace, args.output, args.baseline, args.verdict)


if __name__ == "__main__":
    raise SystemExit(main())
