import { classifyEvent } from "../classify.ts";
import plugin, { messageOf } from "./fatal-error-guard.ts";
import assert from "node:assert/strict";
import test from "node:test";

const apiErr = (statusCode, message = "", responseBody = "") => ({
  name: "APIError",
  data: { message, statusCode, isRetryable: false, responseBody },
});

test("402 APIError → balance", () => {
  const hit = classifyEvent(
    { type: "session.error", properties: { error: apiErr(402) } },
    {},
  );
  assert.equal(hit?.reason, "balance");
});

test("insufficient balance message → balance", () => {
  const hit = classifyEvent(
    {
      type: "session.error",
      properties: { error: apiErr(undefined, "Insufficient balance") },
    },
    {},
  );
  assert.equal(hit?.reason, "balance");
});

test("401 APIError → auth", () => {
  const hit = classifyEvent(
    { type: "session.error", properties: { error: apiErr(401) } },
    {},
  );
  assert.equal(hit?.reason, "auth");
});

test("ProviderAuthError → auth", () => {
  const hit = classifyEvent(
    {
      type: "session.error",
      properties: {
        error: {
          name: "ProviderAuthError",
          data: { providerID: "p", message: "bad key" },
        },
      },
    },
    {},
  );
  assert.equal(hit?.reason, "auth");
  assert.equal(hit?.provider, "p");
});

test("404 APIError → model_unavailable", () => {
  const hit = classifyEvent(
    { type: "session.error", properties: { error: apiErr(404) } },
    {},
  );
  assert.equal(hit?.reason, "model_unavailable");
});

test("model not found message → model_unavailable", () => {
  const hit = classifyEvent(
    {
      type: "session.error",
      properties: { error: apiErr(undefined, "model not found") },
    },
    {},
  );
  assert.equal(hit?.reason, "model_unavailable");
});

test("retry attempt:5 → retry_limit", () => {
  const hit = classifyEvent(
    {
      type: "session.status",
      properties: {
        sessionID: "s",
        status: { type: "retry", attempt: 5, message: "x", next: 1 },
      },
    },
    {},
  );
  assert.equal(hit?.reason, "retry_limit");
});

test("retry attempt:1 (single 429) → null", () => {
  const hit = classifyEvent(
    {
      type: "session.status",
      properties: {
        sessionID: "s",
        status: { type: "retry", attempt: 1, message: "x", next: 1 },
      },
    },
    {},
  );
  assert.equal(hit, null);
});

test("retry with action.reason account_rate_limit → balance", () => {
  const hit = classifyEvent(
    {
      type: "session.status",
      properties: {
        sessionID: "s",
        status: {
          type: "retry",
          attempt: 1,
          message: "weekly usage limit reached",
          next: 1789344000870,
          action: { reason: "account_rate_limit" },
        },
      },
    },
    {},
  );
  assert.equal(hit?.reason, "balance");
});

test("retry message usage limit / available balance → balance", () => {
  const hit = classifyEvent(
    {
      type: "session.status",
      properties: {
        sessionID: "s",
        status: {
          type: "retry",
          attempt: 1,
          message:
            "Weekly usage limit reached. To continue using this model now, enable usage from your available balance",
          next: 1789344000870,
        },
      },
    },
    {},
  );
  assert.equal(hit?.reason, "balance");
});

test("UnknownError model not found → model_unavailable", () => {
  const hit = classifyEvent(
    {
      type: "session.error",
      properties: {
        error: {
          name: "UnknownError",
          data: {
            message: "Model not found: opencode-go-openai/nonexistent-model.",
          },
        },
      },
    },
    {},
  );
  assert.equal(hit?.reason, "model_unavailable");
});

test("single 429 retry with generic message → null", () => {
  const hit = classifyEvent(
    {
      type: "session.status",
      properties: {
        sessionID: "s",
        status: {
          type: "retry",
          attempt: 1,
          message: "rate limit exceeded, try again later",
          next: 1000,
        },
      },
    },
    {},
  );
  assert.equal(hit, null);
});

test("plain text / UnknownError → null", () => {
  assert.equal(
    classifyEvent(
      {
        type: "session.error",
        properties: { error: { name: "UnknownError", data: { message: "x" } } },
      },
      {},
    ),
    null,
  );
  assert.equal(
    classifyEvent({ type: "message.part.updated", properties: {} }, {}),
    null,
  );
});

test("messageOf: plain string message → verbatim", () => {
  assert.equal(
    messageOf(
      "Weekly usage limit reached. To continue using this model now, enable usage from your available balance",
    ),
    "Weekly usage limit reached. To continue using this model now, enable usage from your available balance",
  );
});

test("messageOf: APIError data.message → message", () => {
  assert.equal(
    messageOf(apiErr(402, "Insufficient balance")),
    "Insufficient balance",
  );
});

test("messageOf: non-object → unknown", () => {
  assert.equal(messageOf(null), "unknown");
});

test("hook calls process.exit(1) and writes marker", async () => {
  const tmp = (process.env.RUNNER_TEMP = "/tmp/fatal-guard-test");
  const markerPath = `${tmp}/opencode-fatal.json`;
  const origExit = process.exit;
  let exitCode = null;
  process.exit = (code) => {
    exitCode = code;
    throw new Error("exit");
  };
  try {
    const { mkdirSync } = await import("node:fs");
    mkdirSync(tmp, { recursive: true });
    let handler;
    const context = {
      event: (h) => {
        handler = h;
      },
    };
    if (typeof plugin === "function") {
      const hooks = await plugin({});
      await hooks.event({
        event: {
          type: "session.error",
          properties: { error: apiErr(402, "Insufficient balance") },
        },
      });
    } else if (plugin.setup) {
      await plugin.setup({ context });
      await handler({
        event: {
          type: "session.error",
          properties: { error: apiErr(402, "Insufficient balance") },
        },
      });
    } else {
      throw new Error("unexpected plugin shape");
    }
  } catch {}
  process.exit = origExit;
  assert.equal(exitCode, 1);
  const marker = JSON.parse(
    await import("node:fs").then((fs) => fs.readFileSync(markerPath, "utf8")),
  );
  assert.equal(marker.reason, "balance");
  assert.equal(marker.message, "Insufficient balance");
});
