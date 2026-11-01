#!/usr/bin/env python3
import base64
import json
import os
import re
import urllib.request
from pathlib import Path

OPENAI_CODEX_MODELS_URL = "https://chatgpt.com/backend-api/codex/models?client_version=2.2.5"


def account_id(access_token: str) -> str:
    try:
        encoded_payload = access_token.split(".")[1]
        payload = json.loads(base64.urlsafe_b64decode(encoded_payload + "=" * (-len(encoded_payload) % 4)))
        identifier = payload["https://api.openai.com/auth"]["chatgpt_account_id"]
    except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("OAuth token has no ChatGPT account id") from error
    if not isinstance(identifier, str) or not identifier:
        raise ValueError("OAuth token has no ChatGPT account id")
    return identifier


def model_slugs(access_token: str) -> list[str]:
    configured = os.environ.get("OPENAI_MODEL_SLUGS")
    if configured:
        slugs = configured.splitlines()
    else:
        request = urllib.request.Request(
            OPENAI_CODEX_MODELS_URL,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Originator": "opencode",
                "User-Agent": "opencode/2.2.5",
                "OpenAI-Beta": "responses=experimental",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode())
        slugs = [
            model["slug"]
            for model in payload.get("models", [])
            if isinstance(model, dict)
            and isinstance(model.get("slug"), str)
            and model["slug"]
            and model.get("supported_in_api") is not False
        ]
    slugs = sorted({slug for slug in slugs if re.fullmatch(r"[a-zA-Z0-9._-]+", slug)})
    if not slugs:
        raise ValueError("OpenAI OAuth account returned no supported model slugs")
    return slugs


def write_config() -> tuple[Path, Path]:
    access_token = os.environ["OPENAI_ACCESS_TOKEN"]
    if not isinstance(access_token, str) or not access_token:
        raise ValueError("OAuth credential has no access token")

    caller_key = os.environ["AISIX_CALLER_KEY"]
    if not caller_key:
        raise ValueError("AISIX caller key is empty")

    directory = Path(os.environ["RUNNER_TEMP"]) / "aisix-oauth"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    resources_path = directory / "resources.yaml"
    config_path = directory / "config.yaml"
    model_resources = [
        f'  - display_name: {json.dumps(slug)}\n'
        f'    provider: openai\n'
        f'    model_name: {json.dumps(slug)}\n'
        f'    provider_key: chatgpt-oauth'
        for slug in model_slugs(access_token)
    ]
    resources_path.write_text(
        "\n".join(
            [
                '_format_version: "1"',
                "provider_keys:",
                "  - display_name: chatgpt-oauth",
                "    provider: openai",
                "    adapter: openai",
                "    api_base: https://chatgpt.com/backend-api/codex",
                f"    api_key: {access_token}",
                "    apis:",
                "      responses: null",
                "    request:",
                "      default_headers:",
                f"        ChatGPT-Account-Id: {account_id(access_token)}",
                "        OpenAI-Beta: responses=experimental",
                "        Originator: opencode",
                "        User-Agent: opencode/2.2.5",
                "models:",
                *model_resources,
                "api_keys:",
                "  - display_name: github-actions",
                "    key_env: AISIX_CALLER_KEY",
                '    allowed_models: ["*"]',
                "",
            ]
        ),
        encoding="utf-8",
    )
    config_path.write_text(
        "resources_file: /etc/aisix/resources.yaml\nproxy:\n  addr: 0.0.0.0:3000\nadmin:\n  enabled: false\n",
        encoding="utf-8",
    )
    resources_path.chmod(0o600)
    config_path.chmod(0o600)
    return resources_path, config_path


if __name__ == "__main__":
    resources, config = write_config()
    print(f"AISIX configuration written to {config.parent}")
