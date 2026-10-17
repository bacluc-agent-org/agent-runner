import type {
  ApiError,
  Event,
  EventSessionError,
  EventSessionStatus,
  ProviderAuthError,
} from "@opencode-ai/sdk";

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
  event: Event,
  state: { model?: string },
): FatalHit | null {
  if (event.type === "session.error") {
    const err = event.properties.error;
    if (!err) return null;
    if (err.name === "ProviderAuthError") {
      return {
        fatal: true,
        reason: "auth",
        provider: err.data.providerID,
        error: err,
      };
    }
    if (err.name === "APIError") {
      const data = err.data;
      if (data.statusCode === 401 || data.statusCode === 403) {
        return { fatal: true, reason: "auth", error: err };
      }
      if (data.statusCode === 402) {
        return { fatal: true, reason: "balance", error: err };
      }
      if (data.statusCode === 404) {
        return { fatal: true, reason: "model_unavailable", error: err };
      }
    }
    const data = (err as { data?: { message?: string; responseBody?: string } })
      .data;
    const text =
      `${data?.message ?? ""} ${data?.responseBody ?? ""}`.toLowerCase();
    const reason = matchMessage(text);
    if (reason) return { fatal: true, reason, error: err };
    return null;
  }

  if (event.type === "session.status") {
    const status = event.properties.status;
    if (status.type === "retry") {
      const action = (status as { action?: { reason?: string } }).action;
      if (action?.reason === "account_rate_limit") {
        return { fatal: true, reason: "balance", error: status.message };
      }
      const reason = matchMessage(status.message.toLowerCase());
      if (reason) return { fatal: true, reason, error: status.message };
      if (status.attempt >= 5) {
        return { fatal: true, reason: "retry_limit", error: status.message };
      }
    }
  }

  // OpenCode v2 events
  if (event.type === "session.execution.failed") {
    const properties = event.properties as any;
    if (!properties) return null;
    const error = properties.error;
    if (error) {
      if (error.name === "ProviderAuthError") {
        return {
          fatal: true,
          reason: "auth",
          provider: error.data?.providerID,
          error: error,
        };
      }
      if (error.name === "APIError") {
        const data = error.data;
        if (data?.statusCode === 401 || data?.statusCode === 403) {
          return { fatal: true, reason: "auth", error: error };
        }
        if (data?.statusCode === 402) {
          return { fatal: true, reason: "balance", error: error };
        }
        if (data?.statusCode === 404) {
          return { fatal: true, reason: "model_unavailable", error: error };
        }
      }
      const data = (error as { data?: { message?: string; responseBody?: string } })
        .data;
      const text =
        `${data?.message ?? ""} ${data?.responseBody ?? ""}`.toLowerCase();
      const reason = matchMessage(text);
      if (reason) return { fatal: true, reason, error: error };
    }
    if (properties.reason || properties.message) {
      const text = `${properties.reason ?? ""} ${properties.message ?? ""}`.toLowerCase();
      const reason = matchMessage(text);
      if (reason) return { fatal: true, reason, error: properties };
    }
    return null;
  }

  if (event.type === "session.step.failed") {
    const properties = event.properties as any;
    if (!properties) return null;
    if (properties.reason || properties.message) {
      const text = `${properties.reason ?? ""} ${properties.message ?? ""}`.toLowerCase();
      const reason = matchMessage(text);
      if (reason) return { fatal: true, reason, error: properties };
    }
    return null;
  }

  return null;
}
