import { tool } from "@opencode-ai/plugin";
import { readFileSync, writeFileSync } from "node:fs";
import { isAbsolute, relative, resolve } from "node:path";

const contextPad = 3;

function splitLines(content: string): string[] {
  const lines = content.split("\n");
  if (lines[lines.length - 1] === "") lines.pop();
  return lines;
}

function numberedLines(
  lines: string[],
  from: number,
  to: number,
  markFrom: number,
  markTo: number,
): string {
  const out: string[] = [];
  for (let n = from; n <= to; n++) {
    const marker = n >= markFrom && n <= markTo ? ">" : " ";
    out.push(`${marker}${n} | ${lines[n - 1]}`);
  }
  return out.join("\n");
}

export default tool({
  description:
    "Edit a file with an exact search/replace. 'search' must match the file content exactly (whitespace and indentation included) and must be unique in the file unless all=true. On zero or multiple matches nothing is changed and the error reports the match count plus surrounding context so the search can be corrected. Prefer this over rewriting whole files.",
  args: {
    path: tool.schema
      .string()
      .describe("File to edit, relative to the worktree"),
    search: tool.schema
      .string()
      .describe("Exact text to replace; whitespace must match exactly"),
    replace: tool.schema.string().describe("Replacement text"),
    all: tool.schema
      .boolean()
      .optional()
      .describe("Replace every occurrence instead of requiring a unique match"),
  },
  async execute(args, context) {
    if (args.search === "") {
      return "'search' must not be empty";
    }
    const base = context.worktree ?? context.directory;
    const abs = resolve(base, args.path);
    const rel = relative(base, abs);
    if (rel.startsWith("..") || isAbsolute(rel)) {
      return `Path '${args.path}' is outside the worktree ${base}`;
    }
    let content: string;
    try {
      content = readFileSync(abs, "utf8");
    } catch (err) {
      return `Cannot read '${args.path}': ${(err as Error).message}`;
    }
    const occurrences = content.split(args.search).length - 1;
    if (occurrences === 0) {
      return `'${args.path}' contains 0 matches for the given search string; nothing was changed. Make sure whitespace and indentation match the file exactly.`;
    }
    const lines = splitLines(content);
    const startLine = content
      .slice(0, content.indexOf(args.search))
      .split("\n").length;
    const matchSpan = args.search.split("\n").length - 1;
    if (occurrences > 1 && args.all !== true) {
      const from = Math.max(1, startLine - contextPad);
      const to = Math.min(lines.length, startLine + matchSpan + contextPad);
      return [
        `'${args.path}' contains ${occurrences} matches for the given search string; nothing was changed.`,
        "Extend 'search' with surrounding lines to make it unique, or pass all=true to replace every occurrence.",
        `First match at line ${startLine}:`,
        numberedLines(lines, from, to, startLine, startLine + matchSpan),
      ].join("\n");
    }
    const replaceLiteral = () => args.replace;
    const updated =
      args.all === true
        ? content.replaceAll(args.search, replaceLiteral)
        : content.replace(args.search, replaceLiteral);
    writeFileSync(abs, updated);
    const updatedLines = splitLines(updated);
    const endLine = startLine + args.replace.split("\n").length - 1;
    const from = Math.max(1, startLine - contextPad);
    const to = Math.min(updatedLines.length, endLine + contextPad);
    const where =
      endLine > startLine
        ? `lines ${startLine}-${endLine}`
        : `line ${startLine}`;
    const header =
      occurrences > 1
        ? `Edited '${args.path}', replaced ${occurrences} occurrences, first at line ${startLine}:`
        : `Edited '${args.path}' ${where}:`;
    return `${header}\n${numberedLines(updatedLines, from, to, startLine, endLine)}`;
  },
});
