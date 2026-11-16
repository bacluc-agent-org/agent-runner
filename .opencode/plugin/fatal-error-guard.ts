import { classifyEvent } from "../classify.ts";
import type { Event } from "@opencode-ai/sdk";
import { writeFileSync } from "node:fs";

// .ts extension REQUIRED for Node type stripping

// Total: every input yields a string, so the marker is always well-formed
export function messageOf(error: unknown): string {
  if (typeof error === "string") return error;
  if (!error || typeof error !== "object") return "unknown";
  try {
    const data = (error as any).data ?? error;
    const message =
      typeof data?.message === "string" ? data.message.trim() : "";
    const body =
      typeof data?.responseBody === "string" ? data.responseBody.trim() : "";
    const body2 =
      typeof data?.responseBody === "string"
        ? ""
        : typeof (error as any).response?.body === "string"
          ? (error as any).response.body.trim()
          : typeof data?.response?.body === "string"
            ? data.response.body.trim()
            : "";
    const b = body || body2;
    if (message && b) return `${message} | ${b}`;
    if (message) return message;
    if (b) return b;
    try {
      return JSON.stringify(error) ?? "unknown";
    } catch {
      return "unknown";
    }
  } catch {
    return "unknown";
  }
}

function handleEvent(event: unknown): void {
  const hit = classifyEvent(event as Event, {
    model: process.env.MODEL,
  });
  if (!hit) return;

  const message = messageOf(hit.error);
  console.error(`fatal provider error (${hit.reason}): ${message}`);

  const e = event as any;
  const props = e.properties ?? e.data;
  const sessionID = props?.sessionID ?? e?.properties?.sessionID ?? "unknown";

  const marker = {
    reason: hit.reason,
    model: process.env.MODEL ?? "unknown",
    provider: hit.provider ?? "unknown",
    sessionID,
    error: hit.error ?? null,
    message,
    stage: process.env.OPENCODE_FATAL_STAGE ?? "unknown",
    at: new Date().toISOString(),
  };

  const path = `${process.env.RUNNER_TEMP ?? "/tmp"}/opencode-fatal.json`;
  try {
    writeFileSync(path, JSON.stringify(marker));
  } finally {
    process.exit(1);
  }
}

async function startPullLoop(
  subscribe: (options: { signal: AbortSignal }) => unknown,
  shape: "v1" | "v2",
): Promise<() => void> {
  const controller = new AbortController();
  let result: unknown;
  try {
    result = await subscribe({ signal: controller.signal });
  } catch (error) {
    console.error(
      `fatal-error-guard: subscribing to the ${shape} event stream failed: ${messageOf(error)}`,
    );
    throw error;
  }
  const stream = (result as any)?.stream ?? result;
  if (!stream || typeof stream[Symbol.asyncIterator] !== "function") {
    const detail = stream === undefined ? "undefined" : typeof stream;
    console.error(
      `fatal-error-guard: ${shape} event stream is not async-iterable (got ${detail}); fatal provider-error handling is NOT installed`,
    );
    throw new Error(
      `fatal-error-guard: ${shape} event stream is not async-iterable (got ${detail})`,
    );
  }
  void (async () => {
    try {
      for await (const event of stream) handleEvent(event);
    } catch (error) {
      console.error("fatal-error-guard: event stream failed:", String(error));
    }
  })();
  return () => controller.abort();
}

function describeSetupContext(context: unknown): string {
  if (!context || typeof context !== "object")
    return `context is ${String(context)}`;
  return `context keys [${Object.keys(context).join(", ")}], context.event.subscribe=${typeof (context as any).event?.subscribe}, context.client.event.subscribe=${typeof (context as any).client?.event?.subscribe}`;
}

const plugin = {
  id: "fatal-error-guard",
  // v1 loader requires server(); the host disposes and re-creates plugin state mid-run,
  // and the push hook is the only v1 event path that delivers events in `opencode run`.
  async server(_input: unknown) {
    return {
      event: async ({ event }: { event: Event }) => {
        handleEvent(event);
      },
    };
  },
  // v2: validate the event API before starting the loop; fail setup on incompatible contexts.
  async setup(context: any) {
    if (typeof context?.event?.subscribe === "function") {
      return startPullLoop((options) => context.event.subscribe(options), "v2");
    }
    if (typeof context?.client?.event?.subscribe === "function") {
      return startPullLoop(
        (options) => context.client.event.subscribe(options),
        "v1",
      );
    }
    console.error(
      `fatal-error-guard: no usable event stream in setup context (${describeSetupContext(context)}); expected context.event.subscribe (v2) or context.client.event.subscribe (v1)`,
    );
    throw new Error(
      "fatal-error-guard: no usable event stream in setup context",
    );
  },
};

export default plugin;
