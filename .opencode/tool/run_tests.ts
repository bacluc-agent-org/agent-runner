import { tool } from "@opencode-ai/plugin/tool";
import { spawnSync } from "node:child_process";

const timeoutMinutes = 10;
const tailLines = 200;
const maxBufferBytes = 64 * 1024 * 1024;

export default tool({
  description:
    "Run a test command (e.g. 'uv run pytest -q') in the worktree via bash -lc. Returns the exit code, the total output line count and the last 200 lines of combined stdout/stderr, so long output is truncated visibly. The command is killed after 10 minutes.",
  args: {
    command: tool.schema
      .string()
      .describe("Test command to run, e.g. 'uv run pytest -q'"),
  },
  async execute(args, context) {
    const cwd = context.worktree ?? context.directory;
    const res = spawnSync("/bin/bash", ["-lc", args.command], {
      cwd,
      encoding: "utf8",
      timeout: timeoutMinutes * 60_000,
      maxBuffer: maxBufferBytes,
    });
    const errCode = (res.error as NodeJS.ErrnoException | undefined)?.code;
    if (res.error && errCode !== "ENOBUFS") {
      return `Failed to run '${args.command}': ${res.error.message}`;
    }
    const lines = `${res.stdout ?? ""}${res.stderr ?? ""}`.split("\n");
    if (lines[lines.length - 1] === "") lines.pop();
    const tail = lines.slice(-tailLines);
    const exitCode =
      typeof res.status === "number"
        ? String(res.status)
        : `none (killed by ${res.signal ?? "a signal"}, likely the ${timeoutMinutes} minute timeout)`;
    const shown =
      tail.length < lines.length ? ` (showing last ${tailLines})` : "";
    const overflow =
      errCode === "ENOBUFS"
        ? `\noutput exceeded ${maxBufferBytes / 1024 / 1024}MB and was truncated`
        : "";
    return [
      `exit_code: ${exitCode}`,
      `output lines: ${lines.length}${shown}${overflow}`,
      "",
      tail.join("\n"),
    ].join("\n");
  },
});
