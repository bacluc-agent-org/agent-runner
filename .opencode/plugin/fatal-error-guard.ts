import { classifyEvent } from "../classify.ts";
import type { Plugin } from "@opencode-ai/plugin";
import type { Event } from "@opencode-ai/sdk";
import { writeFileSync } from "node:fs";

// .ts extension REQUIRED for Node type stripping

// Total: every input yields a string, so the marker is always well-formed
export function messageOf(error: unknown): string {
  if (typeof error === "string") return error;
  if (!error || typeof error !== "object") return "unknown";
  const data = (
    error as { data?: { message?: unknown; responseBody?: unknown } }
  ).data;
  const message = typeof data?.message === "string" ? data.message.trim() : "";
  const body =
    typeof data?.responseBody === "string" ? data.responseBody.trim() : "";
  if (message && body) return `${message} | ${body}`;
  if (message) return message;
  if (body) return body;
  try {
    return JSON.stringify(error) ?? "unknown";
  } catch {
    return "unknown";
  }
}

const plugin: Plugin = {
  id: "fatal-error-guard",
  async setup({ context }) {
    context.event(async ({ event }: { event: Event }) => {
      const hit = classifyEvent(event, { model: process.env.MODEL });
      if (!hit) return;

      const message = messageOf(hit.error);
      console.error(`fatal provider error (${hit.reason}): ${message}`);

      const marker = {
        reason: hit.reason,
        model: process.env.MODEL ?? "unknown",
        provider: hit.provider ?? "unknown",
        sessionID:
          (event as { properties?: { sessionID?: string } }).properties
            ?.sessionID ?? "unknown",
        error: hit.error ?? null,
        message,
        stage: process.env.OPENCODE_FATAL_STAGE ?? "unknown",
        at: new Date().toISOString(),
      };

      const path = `${process.env.RUNNER_TEMP ?? "/tmp"}/opencode-fatal.json`;
      try {
        writeFileSync(path, JSON.stringify(marker));
      } finally {
        process.exit(1); // abort always happens even if the write throws
      }
    });
  },
};

export default plugin;
