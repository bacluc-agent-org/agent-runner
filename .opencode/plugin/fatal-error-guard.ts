import { classifyEvent } from "../classify.ts";
import type { Event } from "@opencode-ai/sdk";
import type { Plugin } from "@opencode/plugin/promise/plugin";
import { writeFileSync } from "node:fs";

function exitProcess(): void {
  try {
    process.exit(1);
  } catch {}
}

// .ts extension REQUIRED for Node type stripping

// Total: every input yields a string, so the marker is always well-formed
export function messageOf(error: unknown): string {
  if (typeof error === "string") return error;
  if (!error || typeof error !== "object") return "unknown";
  try {
    if (error instanceof Error && error.message) return error.message;
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

const plugin: Plugin = {
  id: "fatal-error-guard",
  setup(context) {
    const controller = new AbortController();
    void (async () => {
      let stopping = false;
      try {
        const events = await context.event.subscribe({
          signal: controller.signal,
        });
        if (
          !events ||
          typeof (events as any)[Symbol.asyncIterator] !== "function"
        ) {
          throw new TypeError(
            "event subscription did not return an async iterable",
          );
        }
        for await (const event of events) {
          const hit = classifyEvent(event as Event, {
            model: process.env.MODEL,
          });
          if (!hit) continue;

          const message = messageOf(hit.error);
          console.error(`fatal provider error (${hit.reason}): ${message}`);

          const e = event as any;
          const props = e.properties ?? e.data;
          const sessionID =
            props?.sessionID ?? e?.properties?.sessionID ?? "unknown";

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
          stopping = true;
          try {
            writeFileSync(path, JSON.stringify(marker));
          } finally {
            exitProcess();
          }
        }
      } catch (error) {
        if (controller.signal.aborted) return;
        if (stopping) return;
        const marker = {
          reason: "infrastructure",
          kind: "infrastructure_failure",
          model: process.env.MODEL ?? "unknown",
          provider: "unknown",
          sessionID: "unknown",
          error:
            error instanceof Error
              ? { name: error.name, message: error.message }
              : (error ?? null),
          message: messageOf(error),
          stage: process.env.OPENCODE_FATAL_STAGE ?? "unknown",
          at: new Date().toISOString(),
        };
        console.error(
          "fatal-error-guard: infrastructure failure:",
          marker.message,
        );
        stopping = true;
        try {
          writeFileSync(
            `${process.env.RUNNER_TEMP ?? "/tmp"}/opencode-fatal.json`,
            JSON.stringify(marker),
          );
        } finally {
          exitProcess();
        }
      }
    })();
    return () => controller.abort();
  },
};

export default plugin;
