import argparse
import json
import subprocess
from pathlib import Path


NO_CODE_CHANGE = "AGENT_RESULT: NO_CODE_CHANGE"


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
        if any(absolute_path == nested or nested in absolute_path.parents for nested in nested_paths):
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
    root = str(Path(workspace).resolve())
    return {root: repository_state(root)}


def has_delivery(baseline, current):
    for root, state in current.items():
        if root not in baseline:
            return True
        if state["head"] != baseline[root]["head"] or state["status"] != baseline[root]["status"]:
            return True
    return False


def check(workspace, output, baseline_path):
    baseline = json.loads(Path(baseline_path).read_text())
    current = snapshot(workspace)
    if has_delivery(baseline, current):
        print("observable delivery detected")
        return 0
    if NO_CODE_CHANGE in Path(output).read_text().splitlines():
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
    args = parser.parse_args()
    if args.snapshot:
        Path(args.baseline).write_text(json.dumps(snapshot(args.workspace), sort_keys=True))
        return 0
    if not args.output:
        parser.error("--output is required with --check")
    return check(args.workspace, args.output, args.baseline)


if __name__ == "__main__":
    raise SystemExit(main())
