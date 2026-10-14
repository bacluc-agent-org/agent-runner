#!/usr/bin/env python3
"""Run independently modeled agents concurrently from a JSON configuration."""

import argparse
import concurrent.futures
import json
import os
import shutil
import subprocess
import sys


def command(backend, model, prompt, name="coordinator", title=None):
    if backend == "opencode":
        return [
            "opencode", "run", "--agent", name, "--model", model,
            *( ["--title", title] if title else [] ), prompt,
        ]
    if backend == "t3-code":
        raise ValueError("unsupported backend: t3-code")
    raise ValueError(f"unsupported backend: {backend}")


def run_agent(agent, backend):
    args = command(
        backend, agent["model"], agent["prompt"],
        agent.get("name", "coordinator"), agent.get("title"),
    )
    executable = args[0]
    if not shutil.which(executable):
        raise RuntimeError(f"{executable} CLI not found")
    env = os.environ.copy()
    if agent.get("config"):
        if backend == "opencode":
            env["OPENCODE_CONFIG"] = agent["config"]
        else:
            env["T3_CONFIG"] = agent["config"]
    return subprocess.run(
        args,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        env=env,
    ).stdout


def dispatch(agents, backend):
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(agents) or 1) as pool:
        futures = [pool.submit(run_agent, agent, backend) for agent in agents]
        return [future.result() for future in futures]


def execute(config):
    backend = config["backend"]
    try:
        results = dispatch(config["agents"], backend)
    except (subprocess.CalledProcessError, RuntimeError):
        if "fallback" not in config:
            raise
        return [run_agent(config["fallback"], backend)]
    if config.get("judge"):
        judge = dict(config["judge"])
        judge["prompt"] = judge["prompt"].format(outputs="\n\n".join(results))
        try:
            run_agent(judge, backend)
        except (subprocess.CalledProcessError, RuntimeError) as error:
            print(f"subagent runner: optional judge failed: {error}", file=sys.stderr)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    try:
        with open(args.config, encoding="utf-8") as config_file:
            output = execute(json.load(config_file))
        print("\n".join(output))
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"subagent runner: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
