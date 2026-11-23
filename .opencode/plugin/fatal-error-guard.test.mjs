import { classifyEvent } from "../classify.ts";
import plugin, { messageOf } from "./fatal-error-guard.ts";
import assert from "node:assert/strict";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import test from "node:test";

const apiErr = (statusCode, message = "", responseBody = "") => ({
  name: "APIError",
  data: { message, statusCode, isRetryable: false, responseBody },
});

const v2err = (statusCode, message = "", responseBody = "") => ({
  name: "APIError",
  data: { message, statusCode, isRetryable: false, responseBody },
});

const sessionStructuredError = {
  name: "SessionStructuredError",
  data: {
    message: "Output length exceeded the allowed limit",
    responseBody: "limit 5000 bytes",
  },
};

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

test("messageOf: SessionStructuredError → message from data", () => {
  assert.equal(
    messageOf(sessionStructuredError),
    "Output length exceeded the allowed limit | limit 5000 bytes",
  );
});

test("v2 classify: session.execution.failed 402 → balance", () => {
  const hit = classifyEvent(
    {
      type: "session.execution.failed",
      data: { error: v2err(402, "Insufficient balance") },
    },
    {},
  );
  assert.equal(hit?.reason, "balance");
});

test("v2 classify: session.execution.failed provider.auth → auth with provider", () => {
  const hit = classifyEvent(
    {
      type: "session.execution.failed",
      data: { error: { name: "provider.auth", data: { providerID: "p" } } },
    },
    {},
  );
  assert.equal(hit?.reason, "auth");
  assert.equal(hit?.provider, "p");
});

test("v2 classify: session.execution.failed 401 → auth", () => {
  const hit = classifyEvent(
    { type: "session.execution.failed", data: { error: v2err(401) } },
    {},
  );
  assert.equal(hit?.reason, "auth");
});

test("v2 classify: session.execution.failed 403 → auth", () => {
  const hit = classifyEvent(
    { type: "session.execution.failed", data: { error: v2err(403) } },
    {},
  );
  assert.equal(hit?.reason, "auth");
});

test("v2 classify: session.execution.failed 404 → model_unavailable", () => {
  const hit = classifyEvent(
    { type: "session.execution.failed", data: { error: v2err(404) } },
    {},
  );
  assert.equal(hit?.reason, "model_unavailable");
});

test("v2 classify: session.execution.failed message-based → balance", () => {
  const hit = classifyEvent(
    {
      type: "session.execution.failed",
      data: { error: v2err(undefined, "insufficient credit") },
    },
    {},
  );
  assert.equal(hit?.reason, "balance");
});

test("v2 classify: session.execution.failed fallback reason → model_unavailable", () => {
  const hit = classifyEvent(
    {
      type: "session.execution.failed",
      data: { reason: "model not available", message: "" },
    },
    {},
  );
  assert.equal(hit?.reason, "model_unavailable");
});

test("v2 classify: session.step.failed reason → balance", () => {
  const hit = classifyEvent(
    {
      type: "session.step.failed",
      data: { reason: "Insufficient balance" },
    },
    {},
  );
  assert.equal(hit?.reason, "balance");
});

test("v2 classify: session.step.failed message → balance", () => {
  const hit = classifyEvent(
    {
      type: "session.step.failed",
      data: { message: "out of credits" },
    },
    {},
  );
  assert.equal(hit?.reason, "balance");
});

test("plugin setup v2: subscribe captures options.signal, exits on fatal", async () => {
  const tmp = (process.env.RUNNER_TEMP = "/tmp/fatal-guard-test-1");
  const markerPath = `${tmp}/opencode-fatal.json`;
  const origExit = process.exit;
  let exitCode = null;
  process.exit = function (code) {
    exitCode = code;
    throw new Error("exit");
  };
  const cleanup = () => {
    process.exit = origExit;
  };
  try {
    mkdirSync(tmp, { recursive: true });
    const unsub = plugin.setup({
      event: {
        async *subscribe({ signal }) {
          await Promise.resolve();
          yield {
            type: "session.error",
            data: {
              error: apiErr(402, "Insufficient balance"),
              sessionID: "s1",
            },
          };
          if (signal.aborted) return;
        },
      },
    });
    await new Promise((r) => setTimeout(r, 10));
    unsub();
  } catch (e) {
    // ignore exit throw
  }
  cleanup();
  assert.equal(exitCode, 1);
  const marker = JSON.parse(readFileSync(markerPath, "utf8"));
  assert.equal(marker.reason, "balance");
  assert.equal(marker.message, "Insufficient balance");
  assert.equal(marker.sessionID, "s1");
});

test("plugin setup v2: benign event does not exit", async () => {
  const tmp = (process.env.RUNNER_TEMP = "/tmp/fatal-guard-test-2");
  const markerPath = `${tmp}/opencode-fatal.json`;
  const origExit = process.exit;
  let exitCode = null;
  process.exit = function (code) {
    exitCode = code;
    throw new Error("exit");
  };
  const cleanup = () => {
    process.exit = origExit;
  };
  try {
    mkdirSync(tmp, { recursive: true });
    const unsub = plugin.setup({
      event: {
        async *subscribe({ signal }) {
          yield { type: "message.part.updated", data: {} };
        },
      },
    });
    await new Promise((r) => setTimeout(r, 10));
    unsub();
  } catch (e) {
    // ignore
  }
  cleanup();
  assert.equal(exitCode, null);
  try {
    readFileSync(markerPath, "utf8");
    assert.fail("marker should not exist");
  } catch (e) {
    assert.ok(true);
  }
});
