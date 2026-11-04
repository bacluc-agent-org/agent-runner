# agent-runner

This repository runs an automated software agent that turns ideas into merged
pull requests. You write an idea as a GitHub issue, and the agent implements
it and reacts to review comments — all on its own. A branch and pull request
follow when the work changes code; a no-code-change task delivers its result
on the issue instead.

## How it works: two repositories

The system is split across two public repositories:

- [`bacluc-agent/agent-todo`](https://github.com/bacluc-agent/agent-todo) —
  the "idea box". All issues live here.
- [`bacluc-agent-org/agent-runner`](https://github.com/bacluc-agent-org/agent-runner) —
  the "workshop". This repository contains the GitHub Actions workflows that
  run the agent.

There are some example issues in [issues](../../issues) that show how the issues could look like. \
The runner reads issues from the todo repository through the
`ISSUE_REPOSITORY` repository variable (set to `bacluc-agent/agent-todo`).

The workflows were moved here from `agent-todo` in
[PR #170](https://github.com/bacluc-agent/agent-todo/pull/170)
([issue #42](https://github.com/bacluc-agent/agent-todo/issues/42)).

## How an idea becomes an implementation

Everything runs on a schedule in GitHub Actions. No human has to start
anything.

1. **An idea becomes an issue.** Anyone writes an idea as an issue in
   `agent-todo`. It does not need a special format.
2. **The issue is refined** (hourly, at minute 7). The _issue-refiner_ agent
   rewrites the issue body into two clear sections — `## Goal` and
   `## How to implement` — and labels it `ready-for-implementation`.
3. **An issue is selected** (hourly, at minute 24). The _issue-selector_
   agent picks one ready issue, claims it with `agent-running` for the run, and
   writes the implementation prompt for the next step. The claim is removed
   when the run ends.
4. **The issue is implemented** (right after selection). The _coordinator_
   agent checks which AI models are currently available (using a cache so it
   stays fast), picks a model, and implements the issue. It keeps a single
   structured progress comment on the issue. When the work changes code, the
   coordinator pushes the work to its own branch; the workflow pushes
   `agent-run/<issue-number>-<run-id>` only as a fallback for uncommitted work.
5. **A pull request is opened, when code changed.** The coordinator agent opens
   the pull request itself. Its description links the GitHub Actions runs that
   prove the change works.
6. **Review comments are applied** (every 4 hours, at minute 32). The
   _review-fixes_ workflow finds open pull requests with review comments and
   sends the agent back to apply them.
7. **A human merges.** A person reviews the pull request and merges it. Every
   push and pull request is checked by the CI workflow first.

```text
idea (issue in agent-todo)
  → refine (hourly :07)
  → select (hourly :24)
  → implement (coordinator agent)
  → pull request (opened by the agent, when code changed)
  → review comments → fixes (every 4 h)
  → merge (human)
```

## The parts

### Workflows (the schedule)

| Workflow                       | When it runs                                | What it does                                                                                                                                                                                            |
| ------------------------------ | ------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ci.yml`                       | every push to `main` and every pull request | Quality gate: runs the completion check (formatting, workflow lint, tests). Nothing merges if this fails.                                                                                               |
| `refine-issues.yml`            | hourly at minute 7                          | Rewrites vague issues into `## Goal` + `## How to implement` and labels them `ready-for-implementation`.                                                                                                |
| `hourly-issue.yml`             | hourly at minute 24                         | Picks one ready issue, claims it with `agent-running` for the run, and starts the implementation.                                                                                                       |
| `opencode.yml`                 | called by the other workflows               | The core runner: checks model availability, selects a model, runs the coordinator agent, pushes the work, keeps one structured progress comment on the issue, and only short replies to human comments. |
| `review-fixes.yml`             | every 4 hours at minute 32                  | Finds open pull requests with review comments and re-dispatches the agent to apply them.                                                                                                                |
| `refresh-chatgpt-auth.yml`     | 1st and 15th of each month                  | Keeps the OpenAI login working by refreshing the OAuth token (browser login as fallback).                                                                                                               |
| `renew-interaction-limits.yml` | 1st of each month                           | Renews the repository interaction limit so collaborators can keep working.                                                                                                                              |

### Actions (reusable building blocks)

- `setup-opencode` — installs the OpenCode CLI and copies the shared agent
  configuration from the `bacluc/provision-machines` repository (pinned to
  the latest release tag). The AI configuration lives there, not here.
- `model-availability` — probes which AI models currently work and stores the
  result in a cache issue in the todo repository. Probing every model on
  every run would be slow and expensive; the cache makes it cheap.

Selectors apply the shared `scripts/model-deny-list.txt` and stop instead of
dispatching when no allowed model is available.

### Agents (the AI personas)

- `issue-selector` — reads the candidate issues and picks one from the best
  available mechanically ranked tier.

- `issue-refiner` — a technical writer that rewrites issue bodies into
  `## Goal` and `## How to implement`, keeping at most 4000 visible
  characters before any `<details>` fold and moving the rest into one
  `<details><summary>Extra context</summary>` fold inside the
  implementation section.
- `coordinator` and `model-discovery` — the main implementer and the model
  picker. They live in `bacluc/provision-machines` and are installed by the
  `setup-opencode` action.

### Scripts

- `completion-check` — runs all quality checks (see below).
- `validate_refined_issue.py` — checks that a refined issue body has exactly
  the two required sections and at most 4000 visible characters before any
  `<details>` fold (reason `too_long`); extra detail goes into a
  `<details>` fold inside the implementation section.
- `dump_subagent_transcripts.py` — dumps agent session transcripts for
  debugging.
- `refresh-token.py` / `chatgpt-login.py` — refresh the OpenAI login without
  a browser, or fall back to a browser login.
- `issue-selection-tail.txt` — the default selection instructions appended to
  the issue-selector prompt.

## GitHub CLI wrapper

`setup-opencode` installs a Python-stdlib `gh` wrapper in
`$RUNNER_TEMP/gh-cli-wrapper` before starting OpenCode. `GITHUB_PATH` makes it
apply to subsequent steps and child shells, including work in other checkouts.
The installer saves the real CLI's absolute path before changing PATH.

- `gh pr create`, `gh pr new` and the other `gh pr` mutations (`merge`, `close`,
  `edit`, `comment`, `review`, ...) require exactly one explicit `-R`/`--repo`
  targeting `BacLuc`, `bacluc-agent` or `bacluc-agent-org` (case-insensitive) on
  github.com. For outsider repositories, create the fork and a branch representing
  upstream main, then open the PR with `-R bacluc-agent/<repo>` against that branch.
  `gh pr` reads such as `view`, `list` and `diff` stay unguarded.
- Under the nono sandbox GraphQL is denied, so the wrapper translates
  `gh pr create`/`gh pr new` to REST `POST /repos/OWNER/REPO/pulls`. Supported
  options: `-R/--repo`, `-t/--title`, `-b/--body`, `-F/--body-file`,
  `-B/--base`, `-H/--head`, `-d/--draft`; `--title`, `--head` and `--base` are
  required because GraphQL inference is unavailable, and any unmapped option
  fails closed.
- `gh project` mutations with `--owner` require one of those owned organizations.
  A `gh cache delete` key shaped exactly like `owner/repo` is a fail-closed false
  positive (it is mistaken for a repository selector); a key such as
  `key/with/slash` is allowed.
- `gh pr revert` requires exactly one PR number or github.com PR URL and an
  explicit `-R`/`--repo` (or a URL selector) targeting one of those owned owners;
  implicit destinations fail closed. The REST revert endpoint
  `repos/OWNER/REPO/pulls/N/reverts` is checked the same way.
- Mutating builtin commands (`issue`, `repo`, `workflow`, `discussion`, `label`,
  `release`, `secret`, `variable`, `ruleset`, `project`) must resolve to an owned
  repository. `issue` and `workflow` mutations require exactly one explicit
  `-R`/`--repo` or URL selector; the other commands allow at most one and fall
  back to `GH_REPO`, then `GITHUB_REPOSITORY`. `gh repo fork <upstream> --org <owned>` is the one
  sanctioned exception and never validates the upstream positional.
- REST mutations must be a canonical `repos/OWNER/REPO/...` endpoint for an owned
  owner; the fork exception is CLI-only, so `gh api repos/<outsider>/forks -XPOST`
  stays denied. Ambiguous options, noncanonical paths and routing overrides fail
  closed; accepted calls preserve arguments, stdin, output and exit status.
- Direct `gh api graphql`, configured alias execution, alias management and
  extension commands are denied. Use builtin CLI commands or canonical REST
  endpoints instead. Unknown builtin commands/options on guarded routes require
  review before adding support; this is not a complete CLI parser.

This is an accidental-misrouting guardrail, **not a sandbox or universal PR
prevention**. An absolute executable path, direct HTTP, changing PATH or changing
the wrapper can bypass it. Under the nono sandbox GraphQL is denied, so the
wrapper translates `gh pr create`/`gh pr new` to REST
`POST /repos/OWNER/REPO/pulls`; other GraphQL-based builtins (`gh issue view`,
`gh pr view`/`gh pr list`, `gh repo view`, `gh run list`, `gh search`) fail with
HTTP 403 inside the sandbox — use `gh api` REST endpoints instead.

## OpenCode network sandbox

OpenCode runs in `nono` using the pinned profile in
`.github/actions/setup-opencode/nono-profile.json`. Setup installs the pinned
release through `https://nono.sh/install.sh` using the Renovate-managed
`NONO_VERSION`.
The profile extends nono's `node-dev` profile and uses the `developer` network
allowlist plus explicit `api.github.com` and `github.com` endpoint rules:

- GitHub reads (`GET`, `HEAD`, `OPTIONS`) are allowed.
- GitHub API mutations are allowed only below `/repos/<owned-org>/<repo>/...`
  for `BacLuc`, `bacluc-agent`, and `bacluc-agent-org`.
- `POST /repos/*/*/forks` is the one sanctioned outsider mutation:
  `gh repo fork <outsider> --org <owned>` is allowed because forking does not
  modify the upstream repository. Raw `gh api repos/<outsider>/forks -XPOST`
  stays denied; use the CLI command instead.
- Git HTTPS upload-pack (fetch/clone) is allowed for any repository; receive-pack
  (push) is allowed only for those owned organizations.
- GitHub release assets use separate hosts: `uploads.github.com` carries the
  release-asset upload endpoint (owned-repo mutations only, like api.github.com),
  and `release-assets.githubusercontent.com` serves release-asset downloads as a
  plain CONNECT tunnel; `objects.githubusercontent.com` is additionally
  allowlisted for other GitHub content downloads.
- OpenCode v2 runs a managed local service on `127.0.0.1:49374` for the
  `latest` release channel; `network.open_port: [49374]` grants that localhost
  connect+bind. That port is tied to the pinned OpenCode client version
  (`@opencode/cli@2.0.29` in `.github/actions/setup-opencode/action.yml`), so a client
  version or channel change to a different managed-service port needs a profile
  update; the sandbox-evidence PONG probe fails loudly if so. The grant only
  takes effect from nono 0.80.0, because 0.79.0's Linux proxy fallback ignored
  loopback open ports
  ([nolabs-ai/nono#2042](https://github.com/nolabs-ai/nono/pull/2042)).
- The model providers OpenCode needs are additionally host-allowlisted as plain
  CONNECT tunnels, including `opencode.ai`, `chatgpt.com`, `auth.openai.com`,
  `models.dev`, `models.opencode.ai`, and `api.search.brave.com`; the developer
  profile also provides hosts such as `api.openai.com` and `openrouter.ai`.
  Plain host entries do not filter HTTP methods or paths, and the provider APIs
  need methods such as `POST`.
- Endpoint rules are host-specific and default-deny unmatched methods/paths on
  that hostname. They require TLS interception to inspect method/path. When
  interception is active, nono automatically points the standard client CA
  variables (`SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE`, `NODE_EXTRA_CA_CERTS`,
  `CURL_CA_BUNDLE`, `GIT_SSL_CAINFO`) at the generated CA bundle, so no
  `tls_intercept.ca_env_vars` entry is needed in the profile. Other allowed
  hosts use end-to-end CONNECT tunnels. nono 0.80 has no global method default
  or wildcard endpoint policy, so it cannot express “GET to arbitrary domains,
  but no POST” while preserving provider POSTs. No catch-all host is configured;
  this policy does not claim GET-only access to arbitrary domains.
- `filesystem.deny` blocks `/dev`; Docker provides the broader filesystem
  isolation. On Linux, nono covers matching `/dev` paths present when the
  sandbox starts.
- A process that sends work to a Docker daemon through its socket makes the
  daemon's outbound requests outside the sandbox. Do not expose the Docker
  socket to the agent unless daemon egress is independently restricted.

Agents start OpenCode through `scripts/run-agent-sandboxed.sh`, which requires
`NONO_PROFILE` and runs `nono run --profile "$NONO_PROFILE" --allow-cwd` with
the required runtime grants. The wrapper leaves stdout and stderr visible and
appends nono's stderr (including proxy denial diagnostics) to
`$RUNNER_TEMP/nono-proxy.log` without changing the command's exit status. A
stderr sidecar also parses nono denial diagnostics into JSONL at
`$RUNNER_TEMP/http-guard-denials.jsonl`; the original diagnostic remains visible
and is retained verbatim in the proxy log.
Review this log for recurring denials before proposing allowlist additions;
only allow a host or endpoint when the agent workflow needs it. Avoid copying
unreviewed log contents into prompts or committing them.

nono is pinned to 0.80.0. Its `nono run` help supports counted `-v` verbosity,
and its `nono audit show <session> --json` command exposes a completed session's
audit record. Audit files are stored under
`$XDG_STATE_HOME/nono/audit/<session>/` (normally
`~/.local/state/nono/audit/<session>/`), including `audit-events.ndjson`.
Find session IDs with `nono audit list`, then inspect them with
`nono audit show <session> --json`; these files are distinct from the live
stderr capture at `$RUNNER_TEMP/nono-proxy.log` and are created when sessions
finalize. Don't use `nono audit show` as a live-session log reader.

Brave Search MCP access requires `api.search.brave.com`, already present in
`.github/actions/setup-opencode/nono-profile.json`. When adding an MCP provider,
review its required hosts and methods before changing the profile; plain host
allowlist entries are CONNECT access, not method-specific endpoint policy.

This kernel sandbox complements, but does not replace, the `gh` wrapper above.
The profile adds no filesystem grants beyond denying `/dev`. Since nono's
`node-dev` base still uses filesystem capabilities, `scripts/run-agent-sandboxed.sh`
grants OpenCode's state/database paths, read access to its config, the GitHub CLI
wrapper at `$RUNNER_TEMP/gh-cli-wrapper`, and checkout credential includes matching
`$RUNNER_TEMP/git-credentials-*.config`, plus writable npm cache access. The
sandbox wrapper probe uses the same narrow wrapper and credential paths. This keeps the
wrapper executable inside nono while Docker remains the broader filesystem
isolation boundary.
`scripts/http_guard.py` remains an advisory, importable policy helper
(`check_request`) for callers that want to pre-screen a request and log denials;
nono is the enforcement boundary, so the helper is intentionally not wired into
the wrapper installer (defense-in-depth, not the boundary). `check_request`
retains unconditional `GET`, `HEAD`, and `OPTIONS` read semantics and the
sanctioned `POST /repos/*/*/forks` exception. Both direct helper denials and
parsed nono diagnostics use structured records in
`$RUNNER_TEMP/http-guard-denials.jsonl` (falling back to `/tmp` when `RUNNER_TEMP`
is unset).

Regression tests use a fake backend and never create forbidden PRs. Run
`python3 -m unittest discover -s scripts -p test_gh_cli_wrapper.py` and
`python3 -m unittest discover -s scripts -p test_http_guard.py` locally. The
dispatchable CI workflow also runs the actual setup composite with a fake backend
and verifies forwarding and historical denial in a later step outside the checkout.

## Repository variables

Set these in Settings → Secrets and variables → Actions → Variables:

| Variable                         | Description                                                                                                                                | Default                   |
| -------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------- |
| `ISSUE_REPOSITORY`               | Repository for issue tracking and cache (format: `owner/repo`)                                                                             | `github.repository`       |
| `MODEL_AVAILABILITY_CACHE_ISSUE` | Issue number used as the model-availability cache                                                                                          | auto-detected             |
| `FORK_INVITE_USER`               | GitHub user invited with `push` on every new fork (exposed to the `github-fork-invite` skill as `GITHUB_FORK_INVITE_USER`; empty disables) | unset (skill no-op)       |
| `REVIEW_FIXES_OWNER`             | GitHub account (organization or user) whose authored pull requests the _review-fixes_ workflow searches for review comments                | `github.repository_owner` |

## Secrets

Secrets are stored in Settings → Secrets and variables → Actions → Secrets.
Never print their values.

| Group                           | Secret                                                                     | Used for                                                                       |
| ------------------------------- | -------------------------------------------------------------------------- | ------------------------------------------------------------------------------ |
| GitHub access                   | `BACLUC_AGENT_GITHUB_TOKEN`                                                | All issue, PR, and workflow operations                                         |
| GitHub access                   | `FORK_INVITE_USER`                                                         | Fallback for the `FORK_INVITE_USER` variable (variable wins when both are set) |
| AI providers                    | `OPENCODE_GO_API_KEY`, `OPENCODE_GO_2_API_KEY`, `OPENROUTER_API_KEY`       | API keys for the AI model providers                                            |
| OpenAI login                    | `OPENCODE_AUTH_JSON`                                                       | The OpenAI OAuth credential (refreshed automatically)                          |
| Web search                      | `BRAVE_API_KEY`                                                            | API key for the `brave-search` MCP server (optional)                           |
| OpenAI login (ChatGPT fallback) | `CHATGPT_EMAIL`, `CHATGPT_PASSWORD`, `CHATGPT_2FA_KEY`, `CHATGPT_TOTP_KEY` | Browser login fallback with a ChatGPT account                                  |
| OpenAI login (OpenAI fallback)  | `OPENAI_USERNAME`, `OPENAI_PASSWORD`, `OPENAI_2FA_KEY`                     | Browser login fallback with an OpenAI account                                  |

## Completion check

Run `./scripts/completion-check` before pushing. It runs all quality checks
in Docker and exits non-zero if any check fails:

- Prettier formatting check (`prettier --check .`)
- actionlint (workflow syntax)
- OpenCode plugin tests (`node --test`)
- pytest (script tests)

`.github/workflows/ci.yml` runs the same script on every push to `main` and
every pull request.

## Examples: from idea to merged change

The examples below are text excerpts rendered from the real issue, pull
request, and change. They show how an idea becomes an implementation.

### Example 1: in this repository

Issue [bacluc-agent/agent-todo#184](https://github.com/bacluc-agent/agent-todo/issues/184)
→ pull request [bacluc-agent/agent-runner#9](https://github.com/bacluc-agent/agent-runner/pull/9).

**The issue** (excerpt):

```text
## Model-availability check too slow (16–25 min) & cache not updated & OpenRouter not chosen

**Reporter:** coordinator run 34649959505 (model `opencode/big-pickle`), 2026-09-11 21:33–21:59Z.

### Symptoms
1. **`Check model availability` step takes ~16–25 min on every run.** Observed: run 34649959505
   spent 25 min in the model-availability step alone; the whole run was 26 min.
2. **Cache is not updated between runs.** ... the 21:33 run never wrote a fresh cache back
   despite probing for 25 min. Next run therefore re-probes everything again.
3. **OpenRouter models are never chosen.** ... never appear in the model-availability cache
   (0 openrouter entries) and are not in the coordinator's fallback/selection candidate list.

### Desired behavior
- Non-refresh runs must be fast: if the cache has a fresh `ok` entry (within TTL), skip the probe.
- Only when entries are actually stale should a full refresh run take the ~16 min.
- OpenRouter free models should be eligible for selection.

### To do
- [ ] Investigate why `write_cache` fails / cache is not persisted after a successful probe run.
- [ ] Investigate why probes mostly end up `ok:false` (timeout/auth/balance?) ...
- [ ] Make selection include `openrouter` candidates ...
- [ ] Do not probe all models on every run ... the availability check runs in 5 min.
```

**The pull request description** (excerpt):

```text
## Root cause
The model-availability probe had five compounding problems:
1. **Cache never persisted** — the cache was written to a repo path that didn't survive
   between runs, so every workflow run re-probed all models, taking the full 16–25 minutes.
2. **Cache body exceeded the 64KB issue limit** — ... writes failed.
3. **OpenRouter never selected** — the select-model fallback didn't include OpenRouter ...
4. **Unbounded probing** — every stale candidate was probed on every run ... → 16–30 min.
5. **Probes dominated by doomed candidates** — ~68% of probes failed with `Insufficient credits`
   on OpenRouter **paid** models ...

## Proof: step under 5 min
| Run | Cache state | 'Check model availability' duration |
|---|---|---|
| 34665350884 | worst case (all 280 entries aged stale) | **2 min 52 s** |
| 34666623043 | common case (275/280 fresh) | **48 s** |
| 34678823585 | fresh cache (final head, post-fix) | **12 s** |

Contrast — old code (main): hourly run 34665106771 took **29 min 38 s** in the same step.

## Tests
`uv run --group dev pytest -q` → **116 passed**. `./scripts/completion-check` → green.
```

**The change** (excerpt, 6 files, +560/−41):

```diff
 AVAILABLE_TTL_HOURS = 24
-FAILED_TTL_HOURS = 2
+FAILED_TTL_HOURS = 24
 FREE_PATTERNS = [r"(?:-|:)free$", r"big-pickle"]
 PROVIDER_WHITELISTS: dict[str, list[str]] = {
-    "openrouter": [r"(?:-|:)free$", r"big-pickle", r"glm", r"gpt-5\.6-luna", r"qwen", r"kimi"],
+    "openrouter": [r"(?:-|:)free$", r"big-pickle"],
+    "opencode": [r"(?:-|:)free$", r"big-pickle", r"glm", r"gpt-5\.6-luna", r"qwen", r"kimi"],
 }
 MAX_CONCURRENT = 5
-PROBE_TIMEOUT_SECONDS = 60
+PROBE_TIMEOUT_SECONDS = 30
+PROBE_BUDGET = 30
```

The PR description links the GitHub Actions runs that prove the change works — this is the convention required by `AGENTS.md` in this repository. The change reduced the model-availability check from ~16–25 min to under 5 min (and often under 1 min) by fixing cache persistence, adding a probe budget, and including OpenRouter free models in selection.

### Example 2: in a remote repository

This example was solved by the agent running in this repository, working in
the `ecamp/ecamp3` repository. The idea came from an ecamp3 issue, was tracked
in `bacluc-agent/agent-todo`, and the pull request was merged into ecamp3.

Issue [bacluc-agent/agent-todo#152](https://github.com/bacluc-agent/agent-todo/issues/152)
→ pull request [ecamp/ecamp3#10722](https://github.com/ecamp/ecamp3/pull/10722).
ecamp3 is a camp management web app for Swiss youth organizations; the idea
came from [ecamp/ecamp3#10046](https://github.com/ecamp/ecamp3/issues/10046).

**The issue** (excerpt):

```text
## Goal
In the camp admin checklist UI, guests and outsiders must be read-only: the
checklist rename button, item drag-and-drop, and item edit controls must be
hidden/disabled for non-contributors, while members and managers retain full
editing.
```

**The pull request description** (excerpt):

```text
Fixes #10046

## Problem
In the camp admin, guests could still edit checklists: rename them, drag-drop
items, and edit item content. The UI gated editability on `isOutsider` alone,
but a guest has `isOutsider = false` (they have a role — just the wrong one).
Only users with *no* role at all are outsiders.

## Fix
In `frontend/src/components/checklist/ChecklistDetail.vue`, gate editability on
a new `isReadOnly` computed (`isGuest || isOutsider`) instead of `isOutsider`:

- Rename pencil button: `!isOutsider` → `!isReadOnly`
- `debouncedDisabled` (drives drag-drop and item editing via the `disabled`
  prop on `SortableChecklist`/`SortableChecklistItem`): `isOutsider` → `isReadOnly`

Using `isGuest || isOutsider` rather than `!isContributor` preserves editing in
the global admin (`/admin/checklists`), where `camp` is null and all role flags
are false.

## Verification
- `npm run lint:check` passes
- `npm run test:unit` passes (1268 tests, 0 failures)
- Verified `SortableChecklist.vue`/`SortableChecklistItem.vue` honor the
  `disabled` prop (read-only rendering, hidden add/edit controls);
  `ChecklistOverview.vue` already gates on `isContributor`

Tracked in bacluc-agent/agent-todo#152.
```

**The change** (excerpt, 1 file, +5/−2):

```diff
--- a/frontend/src/components/checklist/ChecklistDetail.vue
+++ b/frontend/src/components/checklist/ChecklistDetail.vue
@@ -10,7 +10,7 @@
      <v-toolbar-title v-if="!editChecklistName" tag="h1" class="font-weight-bold ml-0">
        {{ checklist.name }}
        <v-btn
-          v-if="!editChecklistName && !isOutsider"
+          v-if="!editChecklistName && !isReadOnly"
          icon
          class="ml-1 visible-on-hover"
          width="24"
@@ -116,6 +116,9 @@ export default {
    items() {
      return this.checklist.checklistItems().items.filter((item) => !item.parent)
    },
+    isReadOnly() {
+      return this.isGuest || this.isOutsider
+    },
  },
  async mounted() {
    await this.api
@@ -127,7 +130,7 @@ export default {
      .$loadItems()

    await nextTick()
-    this.debouncedDisabled = this.isOutsider
+    this.debouncedDisabled = this.isReadOnly
  },
  methods: {
    checklistRoute,
```

## FAQ

- **Why two repositories?** The main reason is security: you can't restrict
  what other people can post in your issues (you can set an interaction limit,
  but that is not enough). The issues (the "what") are separated from the
  machinery (the "how") so the todo repository stays readable and the runner
  can be reused.
- **Where does the AI configuration live?** In `bacluc/provision-machines`;
  the `setup-opencode` action installs it from the latest release tag.
- **How do I add an idea?** You don't — only `@BacLuc` does for this repository.
  You are free to fork this repository and point it to another issue repo.
  Open an issue in `bacluc-agent/agent-todo`. The refiner will turn it into a
  precise task.
- **How do I know what the agent is doing?** The agent keeps a single
  structured comment on the issue and updates it in place: management
  summary, design decisions, open pull request, verification evidence, and
  a table of action runs. It posts a new comment only as a short reply to
  a comment you wrote.
- **Who merges the pull request?** Mostly a human. In this repo, the agent
  already merged things by himself. The agent implements the change and opens
  the PR when the work changes code; a person reviews and merges.
