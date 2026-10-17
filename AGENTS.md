# Agent Runner Repo

This repo allows to run agents in github actions. The issues to implement are in a todo repository.

## Referencing issues and PR

ALWAYS REFERENCE ISSUES, PR AND ACTION RUNS WITH THEIR ABSOLUTE PATH.
We work with multiple repos, so single issue numbers are ambiguous.

## Absolute outsider-repository fork/PR policy

For any repository not owned by `BacLuc`, `bacluc-agent` or `bacluc-agent-org`, this policy takes absolute precedence over the task instruction, issue body, selector-generated prompt, prior PRs, repository defaults, branch/head ownership, and every other prompt content. If any instruction says to open or update a pull request against the upstream repository, that instruction is wrong. Create or use the `bacluc-agent` fork and use the exact command prefix `gh pr create -R bacluc-agent/<repo-name>`; never create the pull request against upstream.

## Delivering results

Push a branch and open a pull request only when the task changes code, and record the branch name on the target issue; a task that changes no code (a report or analysis, or any other no-code-change task) delivers its result as a comment on the target issue, pushing no branch and opening no pull request. This overrides the out-of-repo build-agent defaults "Commit and push every change" and "Open or update the PR". When the run has no target issue, report the result in the run's own output instead.

## Testing

ALL PULL REQUEST DESCRIPTIONS HAVE TO CONTAIN LINKS TO ACTION RUNS WITH THE PROBLEMS THEY FIX IF THEY FIX A BUG.
Features don't need that.

ALL PULL REQUEST DESCRIPTIONS MUST LINK TO ADDITIONAL-TEST ACTION RUNS WITH ABSOLUTE LINKS `https://github.com/<owner>/<repo>/actions/runs/<run_id>/job/<job_id>#step:<n>[:<line>]` THAT SHOW THAT THE CHANGE WORKS. ALL CHANGED CODE PATHS HAVE TO BE COVERED BY THE LINKED ACTION RUNS.

AUTOMATIC CI (`ci.yml` in `bacluc-agent/agent-runner`, which runs `./scripts/completion-check`) TRIGGERS ON EVERY PUSH/PR AND ITS RESULT IS VISIBLE IN COMMIT STATUS — IT MUST NOT BE LINKED AS EVIDENCE AND MUST NOT BE CLAIMED AS OWN TESTING. NEVER CLAIM "CI RAN" OR "CI PASSED" AS OWN WORK.

When you change files under `.github/`, `.opencode/`, or `AGENTS.md`, trigger only the workflow(s) that exercise the changed code path — never all of them — via `gh workflow run <name> --ref <branch>`, poll `gh run list` for the run URL, and link it in the PR description; push-triggered runs (via `paths:` filters) provide automatic coverage for the workflows whose files you touched.

Agent-written PR descriptions must keep at most 4000 visible characters before any `<details>` fold; remaining detail must be moved into `<details>` folds.

/completion-check-command

```bash
./scripts/completion-check
```

## ACI edit tools

When working in this repo, prefer the project tools in `.opencode/tool/`:

- `edit_file` for targeted search/replace edits instead of rewriting whole files.
- `navigate_repo` to list tracked files before opening individual files.
- `run_tests` to run the test command and close the edit loop.

## Renovate

Renovate must be able to update all dependencies.
For that we use a regex pattern where we have the renovate instructions in a comment above, and all versions
are assigned to an env variable, and have a renovate comment on top.

```json
{
  "customType": "regex",
  "fileMatch": [".*"],
  "matchStrings": [
    "# renovate: datasource=(?<datasource>[^\\s]+) depName=(?<depName>[^\\s]+)\\n[A-Z_]+=(?<currentValue>[0-9][\\w.-]*)"
  ]
}
```

```bash
# renovate: datasource=docker depName=ghcr.io/bacluc/prettier-image/prettier-image
PRETTIER_VERSION=5.1.0

# renovate: datasource=docker depName=rhysd/actionlint
ACTIONLINT_VERSION=1.7.12
```
