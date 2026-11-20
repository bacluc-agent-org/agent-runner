def fork_slug(owner: str, repo: str) -> str:
    if owner.lower() not in {"bacluc", "bacluc-agent", "bacluc-agent-org"}:
        return f"bacluc-agent/{repo}"
    return f"{owner}/{repo}"
