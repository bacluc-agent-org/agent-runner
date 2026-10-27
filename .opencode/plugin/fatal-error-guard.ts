import { classifyEvent } from "../classify.ts";
import type { Event } from "@opencode-ai/sdk";
import type { Plugin } from "@opencode/plugin/promise/plugin";
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

const plugin: Plugin = {
  id: "fatal-error-guard",
  setup(context) {
    const opts = context.options as any;
    const namespace = opts?.namespace ?? null;
    const labels = opts?.labels ?? null;
    const annotations = opts?.annotations ?? null;
    const controller = new AbortController();
    void (async () => {
      for await (const event of context.event.subscribe({
        signal: controller.signal,
      })) {
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
          namespace,
          labels,
          annotations,
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
    })().catch((e) =>
      console.error("fatal-error-guard: event stream failed:", String(e)),
    );
    return () => controller.abort();
  },
};

export default plugin;
