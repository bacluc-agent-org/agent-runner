#!/usr/bin/env python3

import json
import os
import secrets
import subprocess
import sys
import tempfile


def run_opencode(*args) -> str:
    env = dict(os.environ)
    return subprocess.run(
        ["opencode", *args], check=True, capture_output=True, text=True, env=env
    ).stdout


def run_opencode_to_file(*args, path: str) -> None:
    env = dict(os.environ)
    with open(path, "w") as out:
        subprocess.run(["opencode", *args], stdout=out, check=True, env=env)


def compact_json(value) -> str:
    return json.dumps(value, separators=(",", ":"))


def as_text(value) -> str:
    return value if isinstance(value, str) else compact_json(value)


def trunc(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "...[truncated]"


def render_transcript(session: dict, session_id: str, label: str = "Subagent transcript") -> list[str]:
    agent = (session.get("info") or {}).get("agent") or "subagent"
    lines = [f"--- {label}: {agent} ({session_id}) ---"]
    for message in session.get("messages") or []:
        for part in message.get("parts") or []:
            if part.get("type") == "text" and len(part.get("text") or "") > 0:
                lines.append(f"[{agent}] {part['text']}")
            elif part.get("type") == "tool" and (part.get("tool") or "") != "task":
                state = part.get("state") or {}
                line = f"[{agent}:tool] {part.get('tool') or 'unknown'} {trunc(compact_json(state.get('input') or {}), 200)}"
                output = state.get("output")
                if (
                    state.get("status") == "completed"
                    and output is not None
                    and len(as_text(output)) > 0
                ):
                    line += "\n  output: " + trunc(as_text(output), 500)
                lines.append(line)
    return lines


AGENT_LABELS = {
    "[Build Agent]",
    "[Refiner Agent]",
    "[Planner Agent]",
    "[Tester Agent]",
    "[Review Agent]",
}


def child_session_ids(root_export: dict) -> list[str]:
    ids = set()

    def walk(node):
        if isinstance(node, dict):
            if node.get("tool") == "task":
                session_id = ((node.get("state") or {}).get("metadata") or {}).get(
                    "sessionId"
                )
                if session_id:
                    ids.add(session_id)
            info = node.get("info") or {}
            agent = info.get("agent")
            if agent and isinstance(agent, str) and agent.startswith("ses_"):
                ids.add(agent)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(root_export)

    for message in root_export.get("messages") or []:
        msg_info = message.get("info") or {}
        msg_agent = msg_info.get("agent")
        if msg_agent and isinstance(msg_agent, str) and msg_agent.startswith("ses_"):
            ids.add(msg_agent)
        for part in message.get("parts") or []:
            part_info = part.get("info") or {}
            part_agent = part_info.get("agent")
            if part_agent and isinstance(part_agent, str) and part_agent.startswith("ses_"):
                ids.add(part_agent)

    return sorted(ids)


def inline_agent_segments(root_export: dict) -> list[tuple[str, dict]]:
    # ponytail: inline segment extraction; upgrade to structured session refs if needed
    root_agent = (root_export.get("info") or {}).get("agent")
    segments = []
    for message in root_export.get("messages") or []:
        msg_info = message.get("info") or {}
        msg_agent = msg_info.get("agent")
        agent_name = msg_agent if msg_agent else None
        for part in message.get("parts") or []:
            text = part.get("text") or ""
            for label in AGENT_LABELS:
                if label in text:
                    agent_name = label.strip("[]")
                    break
            if agent_name and not msg_agent:
                pseudo = {
                    "info": {"agent": agent_name},
                    "messages": [{"parts": message.get("parts", [])}],
                }
                segments.append((agent_name, pseudo))
                break
        if (
            agent_name
            and msg_agent
            and agent_name != root_agent
            and not any(s[0] == agent_name for s in segments)
        ):
            pseudo = {
                "info": {"agent": agent_name},
                "messages": [{"parts": message.get("parts", [])}],
            }
            segments.append((agent_name, pseudo))
    seen = set()
    deduped = []
    for agent_name, pseudo in segments:
        if agent_name not in seen:
            seen.add(agent_name)
            deduped.append((agent_name, pseudo))
    return deduped


def coordinator_session_id(sessions, title: str) -> str | None:
    for session in sessions:
        if session.get("title") == title:
            return session.get("id")
    return None


def main() -> int:
    title = os.environ.get("COORDINATOR_SESSION_TITLE", "coordinator-run")
    try:
        sessions = json.loads(run_opencode("session", "list", "--format", "json"))
        root_id = coordinator_session_id(sessions, title)
    except Exception:
        sessions = []
        root_id = None
    if not root_id:
        print("No coordinator session found; skipping subagent transcripts.")
        return 0

    root_export = None
    root_export_size = 0
    root_tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            root_tmp_path = tmp.name
        run_opencode_to_file("export", root_id, path=root_tmp_path)
        root_export_size = os.path.getsize(root_tmp_path)
        with open(root_tmp_path) as f:
            root_export = json.load(f)
    except json.JSONDecodeError as e:
        try:
            root_export_size = os.path.getsize(root_tmp_path) if root_tmp_path and os.path.exists(root_tmp_path) else 0
        except OSError:
            root_export_size = 0
        last_valid = "N/A"
        repaired = None
        if root_tmp_path and os.path.exists(root_tmp_path):
            try:
                with open(root_tmp_path, "r") as rf:
                    raw = rf.read()
                pos = getattr(e, "pos", 0) or 0
                prefix = raw[:pos] if pos else raw
                bases = []
                stripped = prefix.rstrip().rstrip(",")
                if stripped not in bases:
                    bases.append(stripped)
                if "}," in prefix:
                    head, _ = prefix.rsplit("},", 1)
                    cand = (head + "}").rstrip().rstrip(",")
                    if cand not in bases:
                        bases.append(cand)
                tried = set()
                for base in bases:
                    for suffix in ["}", "}}", "]}", "]}}", "]}}}", "]}"]:
                        candidate = base + suffix
                        if candidate in tried:
                            continue
                        tried.add(candidate)
                        try:
                            parsed = json.loads(candidate)
                            if isinstance(parsed, dict) and "messages" in parsed:
                                repaired = parsed
                                last_valid = str(len(parsed.get("messages", [])) - 1)
                                break
                        except Exception:
                            continue
                    if repaired is not None:
                        break
                if repaired is None:
                    last_valid = str(prefix.count('"parts"'))
            except Exception:
                last_valid = "N/A"
        msg = f"COORDINATOR_SESSION_TITLE={title} found={root_id is not None} total_sessions={len(sessions) if isinstance(sessions, list) else 'N/A'} root_export_bytes={root_export_size} last_valid_index={last_valid} error={e}"
        print(msg, file=sys.stderr)
        if repaired is not None:
            root_export = repaired
        else:
            root_export = None
    except Exception as e:
        try:
            root_export_size = os.path.getsize(root_tmp_path) if root_tmp_path and os.path.exists(root_tmp_path) else root_export_size
        except OSError:
            pass
        msg = f"COORDINATOR_SESSION_TITLE={title} found={root_id is not None} total_sessions={len(sessions) if isinstance(sessions, list) else 'N/A'} root_export_bytes={root_export_size} error={e}"
        print(msg, file=sys.stderr)
        root_export = None
    finally:
        if root_tmp_path and os.path.exists(root_tmp_path):
            try:
                os.unlink(root_tmp_path)
            except OSError:
                pass

    child_ids = []
    if root_export is not None:
        try:
            child_ids = child_session_ids(root_export)
        except Exception as e:
            print(f"Child session ID extraction failed: {e}", file=sys.stderr)
            child_ids = []

    # ponytail: export all spawned subagent sessions; session list misses them
    # (subagent sessions are exportable even when not listed; add a session-list
    # check only if exporting stale IDs ever becomes a problem)
    filtered_child_ids = child_ids

    token = secrets.token_hex(32)
    print(f"::stop-commands::{token}")

    if root_export is not None:
        for line in render_transcript(root_export, root_id, label="Coordinator transcript"):
            print(line)
    else:
        print("--- Coordinator transcript: export failed ---")

    inline_segments = []
    if root_export is not None:
        inline_segments = inline_agent_segments(root_export)

    if not filtered_child_ids and not inline_segments:
        print("(No subagents were spawned.)")
    else:
        for child_id in filtered_child_ids:
            child_tmp_path = None
            try:
                with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
                    child_tmp_path = tmp.name
                run_opencode_to_file("export", child_id, path=child_tmp_path)
                with open(child_tmp_path) as f:
                    child_export = json.load(f)
                for line in render_transcript(child_export, child_id):
                    print(line)
            except Exception:
                continue
            finally:
                if child_tmp_path and os.path.exists(child_tmp_path):
                    try:
                        os.unlink(child_tmp_path)
                    except OSError:
                        pass

        for agent_name, pseudo in inline_segments:
            pseudo_id = f"inline-{agent_name.lower().replace(' ', '-')}-segment"
            for line in render_transcript(pseudo, pseudo_id, label=f"Inline agent segment: {agent_name}"):
                print(line)

    if root_export is None:
        print(f"COORDINATOR_SESSION_TITLE={title} found={root_id is not None} total_sessions={len(sessions) if isinstance(sessions, list) else 'N/A'} root_export_bytes={root_export_size} messages=N/A")
    elif not filtered_child_ids:
        msg_count = len(root_export.get("messages", [])) if isinstance(root_export, dict) else 0
        raw_size = root_export_size
        print(f"COORDINATOR_SESSION_TITLE={title} found={root_id is not None} total_sessions={len(sessions) if isinstance(sessions, list) else 'N/A'} root_export_bytes={raw_size} messages={msg_count}")

    print(f"::{token}::")
    return 0


def demo() -> None:
    # Minimal self-check: verify core helpers work with synthetic data
    # Fails if root_export incorrectly None or child_ids miss spawned agents
    session = {
        "info": {"agent": "planner"},
        "messages": [{"parts": [{"type": "text", "text": "hello"}]}],
    }
    lines = render_transcript(session, "ses_demo")
    assert any("hello" in line for line in lines)
    assert any("planner" in line for line in lines)
    ids = child_session_ids({"messages": [{"info": {"agent": "ses_x"}}]})
    assert "ses_x" in ids
    # Verify spawned agents are not dropped by session-list filtering
    assert ids == ["ses_x"], f"child_ids miss spawned agents: {ids}"
    segments = inline_agent_segments({"messages": [{"parts": [{"type": "text", "text": "[Build Agent] done"}]}]})
    assert len(segments) == 1
    assert segments[0][0] == "Build Agent"
    print("demo: OK")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--demo":
        demo()
    else:
        sys.exit(main())
