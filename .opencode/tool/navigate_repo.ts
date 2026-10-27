import { tool } from "@opencode-ai/plugin";
import { spawnSync } from "node:child_process";
import { isAbsolute, relative, resolve } from "node:path";

const defaultLimit = 200;

export default tool({
  description:
    "List files tracked by git under a directory of the repository (defaults to the repo root). Bounded output: at most 'limit' entries are shown and one summary line reports how many entries were hidden. Use this to explore the repository structure instead of dumping whole directory trees.",
  args: {
    path: tool.schema
      .string()
      .optional()
      .describe(
        "Subdirectory whose tracked files to list, relative to the worktree; defaults to the repo root",
      ),
    limit: tool.schema
      .number()
      .int()
      .min(1)
      .default(defaultLimit)
      .describe("Maximum number of entries to list"),
  },
  async execute(args, context) {
    const ns = (context as any).namespace ?? null;
    const labels = (context as any).labels ?? null;
    const annotations = (context as any).annotations ?? null;
    const base = context.worktree ?? context.directory;
    const target = args.path ?? ".";
    const rel = relative(base, resolve(base, target));
    if (rel.startsWith("..") || isAbsolute(rel)) {
      return `Path '${target}' is outside the worktree ${base}`;
    }
    const res = spawnSync("git", ["ls-files", "--", rel || "."], {
      cwd: base,
      encoding: "utf8",
    });
    if (res.error) {
      return `Failed to run git ls-files: ${res.error.message}`;
    }
    if (res.status !== 0) {
      return `git ls-files failed with exit code ${res.status}: ${res.stderr.trim()}`;
    }
    const entries = res.stdout.split("\n").filter((entry) => entry !== "");
    if (entries.length === 0) {
      return `No tracked files under '${target}'.`;
    }
    const limit = args.limit ?? defaultLimit;
    const hidden = entries.length - Math.min(entries.length, limit);
    const lines = entries.slice(0, limit);
    if (hidden > 0) {
      lines.push(
        `... ${hidden} entries hidden (raise 'limit' or narrow 'path')`,
      );
    }
    const meta = [ns, labels, annotations].filter(Boolean).length
      ? `\n[namespace=${ns ?? "none"}, labels=${labels ?? "none"}, annotations=${annotations ?? "none"}]`
      : "";
    return lines.join("\n") + meta;
  },
});
