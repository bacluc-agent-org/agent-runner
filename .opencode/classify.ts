import type { Event, ProviderAuthError } from "@opencode-ai/sdk";

// AnyEvent: union to support v1 (properties) vs v2 (data) payload shapes and error variations
export type AnyEvent = Event | { type: string; properties?: any; data?: any };

export type FatalReason =
  "auth" | "balance" | "model_unavailable" | "retry_limit";

export interface FatalHit {
  fatal: true;
  reason: FatalReason;
  model?: string;
  provider?: string;
  error?: unknown;
}

const BALANCE_RE =
  /insufficient (balance|credit)|out of (balance|credit)|quota|billing|usage limit|available balance/;
const MODEL_UNAVAILABLE_RE =
  /model .*not (found|available|exist)|no such model|no endpoints found/;

function matchMessage(text: string): "balance" | "model_unavailable" | null {
  if (BALANCE_RE.test(text)) return "balance";
  if (MODEL_UNAVAILABLE_RE.test(text)) return "model_unavailable";
  return null;
}

export function classifyEvent(
  event: AnyEvent,
  state: { model?: string },
): FatalHit | null {
  const e = event as { type: string; properties?: any; data?: any };
  const props = e.properties ?? e.data;

  // Message-based matching for runtime errors and provider errors
  if (e.type === "session.error" || e.type === "session.execution.failed") {
    const err = props?.error;
    if (err) {
      // ProviderAuthError OR provider.auth name
      if (err.name === "ProviderAuthError" || err.name === "provider.auth") {
        const provider =
          err.data?.providerID ?? err.providerID ?? props?.providerID;
        return {
          fatal: true,
          reason: "auth",
          provider,
          error: err,
        };
      }
      if (err.name === "APIError" || err.type === "APIError") {
        const data = err.data;
        const statusCode = data?.statusCode ?? err.status;
        if (statusCode === 401 || statusCode === 403) {
          return { fatal: true, reason: "auth", error: err };
        }
        if (statusCode === 402) {
          return { fatal: true, reason: "balance", error: err };
        }
        if (statusCode === 404) {
          return { fatal: true, reason: "model_unavailable", error: err };
        }
      }
      // Runtime-only field inspection for embedded message/response body
      const data = err.data ?? err;
      const text =
        `${data?.message ?? err.message ?? ""} ${data?.responseBody ?? err.response?.body ?? ""}`.toLowerCase();
      const reason = matchMessage(text);
      if (reason) return { fatal: true, reason, error: err };
    }
    // Fallback to reason/message on the event properties/data
    if (props?.reason || props?.message) {
      const text =
        `${props?.reason ?? ""} ${props?.message ?? ""}`.toLowerCase();
      const reason = matchMessage(text);
      if (reason) return { fatal: true, reason, error: props };
    }
    return null;
  }

  if (e.type === "session.status") {
    const status = props?.status;
    if (status?.type === "retry") {
      const action = status.action;
      // account_rate_limit maps to balance
      if (action?.reason === "account_rate_limit") {
        return { fatal: true, reason: "balance", error: status.message };
      }
      // Message-based matching…
      const reason = matchMessage(String(status.message ?? "").toLowerCase());
      if (reason) return { fatal: true, reason, error: status.message };
      if (status.attempt >= 5) {
        return { fatal: true, reason: "retry_limit", error: status.message };
      }
    }
    return null;
  }

  if (e.type === "session.step.failed") {
    const error = props?.error;
    if (error) {
      const data = error.data ?? error;
      const text =
        `${data?.message ?? error.message ?? ""} ${data?.responseBody ?? error.response?.body ?? ""}`.toLowerCase();
      const reason = matchMessage(text);
      if (reason) return { fatal: true, reason, error };
    }
    if (props?.reason || props?.message) {
      const text =
        `${props?.reason ?? ""} ${props?.message ?? ""}`.toLowerCase();
      const reason = matchMessage(text);
      if (reason) return { fatal: true, reason, error: props };
    }
    return null;
  }

  return null;
}
