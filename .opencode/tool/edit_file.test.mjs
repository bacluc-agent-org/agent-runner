import editFile from "./edit_file.ts";
import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

const mkCtx = () => {
  const worktree = mkdtempSync(join(tmpdir(), "edit-file-"));
  return { worktree, directory: worktree };
};

test("unique match edits the file and reports the line", async (t) => {
  const ctx = mkCtx();
  t.after(() => rmSync(ctx.worktree, { recursive: true, force: true }));
  const file = join(ctx.worktree, "a.txt");
  writeFileSync(file, "alpha\nbeta\ngamma\n");

  const res = await editFile.execute(
    { path: "a.txt", search: "beta", replace: "BETA" },
    ctx,
  );

  assert.ok(typeof res === "string");
  assert.equal(readFileSync(file, "utf8"), "alpha\nBETA\ngamma\n");
  assert.match(res, /Edited 'a\.txt' line 2/);
});

test("ambiguous match returns an error with context and leaves the file untouched", async (t) => {
  const ctx = mkCtx();
  t.after(() => rmSync(ctx.worktree, { recursive: true, force: true }));
  const file = join(ctx.worktree, "a.txt");
  const content = "AAA\nneedle\nBBB\nneedle\nCCC\n";
  writeFileSync(file, content);

  const res = await editFile.execute(
    { path: "a.txt", search: "needle", replace: "x" },
    ctx,
  );

  assert.ok(typeof res === "string");
  assert.match(res, /2 matches/);
  assert.match(res, /line 2/);
  assert.ok(res.includes("AAA"));
  assert.ok(res.includes("BBB"));
  assert.equal(readFileSync(file, "utf8"), content);
});

test("no match returns an error and leaves the file untouched", async (t) => {
  const ctx = mkCtx();
  t.after(() => rmSync(ctx.worktree, { recursive: true, force: true }));
  const file = join(ctx.worktree, "a.txt");
  writeFileSync(file, "AAA\nBBB\n");

  const res = await editFile.execute(
    { path: "a.txt", search: "needle", replace: "x" },
    ctx,
  );

  assert.ok(typeof res === "string");
  assert.match(res, /0 matches/);
  assert.equal(readFileSync(file, "utf8"), "AAA\nBBB\n");
});

test("empty search returns an error and leaves the file untouched", async (t) => {
  const ctx = mkCtx();
  t.after(() => rmSync(ctx.worktree, { recursive: true, force: true }));
  const file = join(ctx.worktree, "a.txt");
  writeFileSync(file, "AAA\nBBB\n");

  const res = await editFile.execute(
    { path: "a.txt", search: "", replace: "x" },
    ctx,
  );

  assert.ok(typeof res === "string");
  assert.match(res, /must not be empty/);
  assert.equal(readFileSync(file, "utf8"), "AAA\nBBB\n");
});

test("all=true replaces every occurrence", async (t) => {
  const ctx = mkCtx();
  t.after(() => rmSync(ctx.worktree, { recursive: true, force: true }));
  const file = join(ctx.worktree, "a.txt");
  writeFileSync(file, "a\nneedle\nb\nneedle\nc\n");

  const res = await editFile.execute(
    { path: "a.txt", search: "needle", replace: "NEW", all: true },
    ctx,
  );

  assert.equal(readFileSync(file, "utf8"), "a\nNEW\nb\nNEW\nc\n");
  assert.match(res, /2 occurrences/);
});

test("path escaping the worktree is rejected", async (t) => {
  const ctx = mkCtx();
  t.after(() => rmSync(ctx.worktree, { recursive: true, force: true }));
  const file = join(ctx.worktree, "..", "edit-file-outside.txt");
  writeFileSync(file, "keep me\n");

  const res = await editFile.execute(
    { path: "../edit-file-outside.txt", search: "keep", replace: "changed" },
    ctx,
  );

  assert.ok(typeof res === "string");
  assert.match(res, /outside the worktree/);
  assert.equal(readFileSync(file, "utf8"), "keep me\n");
});
