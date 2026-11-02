import { tool } from "@opencode-ai/plugin/tool";
import { writeFileSync, appendFileSync } from "node:fs";

export interface LoopMetrics {
  steps: number;
  tokenCostPerStep: number;
  convergenceRate: number;
  failureMode: "success" | "max-steps" | "error" | "timeout" | "unknown";
  terminatedAt: string;
}

export function computeMetrics(
  steps: number,
  progressDelta: number,
  failureMode: LoopMetrics["failureMode"] = "unknown",
  tokenEstimate?: number,
): LoopMetrics {
  // ponytail: token cost estimated from model-discovery output or plumbed from context; upgrade when real token usage API available
  const tokenCostPerStep = tokenEstimate ?? Math.max(0.001, steps * 0.0005);
  // convergenceRate = steps / progressDelta when progressDelta > 0, else steps (unbounded;
  // deviation from the issue's 0-1 `1 - step_count/max_steps` formula, documented in the PR description).
  const convergenceRate = progressDelta > 0 ? steps / progressDelta : steps;
  return {
    steps,
    tokenCostPerStep,
    convergenceRate,
    failureMode,
    terminatedAt: new Date().toISOString(),
  };
}

export function emitMetrics(
  metrics: LoopMetrics,
  summaryPath?: string,
  artifactPath?: string,
): { summary: string; artifact: string } {
  const summaryFile =
    summaryPath ?? process.env.GITHUB_STEP_SUMMARY ?? "/tmp/loop-summary.md";
  const artifactFile = artifactPath ?? "loop-metrics.json";

  const table = [
    "| Metric | Value |",
    "|---|---|",
    `| Steps | ${metrics.steps} |`,
    `| Token cost / step | ${metrics.tokenCostPerStep.toFixed(4)} |`,
    `| Convergence rate | ${metrics.convergenceRate.toFixed(4)} |`,
    `| Failure mode | ${metrics.failureMode} |`,
    `| Terminated at | ${metrics.terminatedAt} |`,
  ].join("\n");

  appendFileSync(summaryFile, "\n" + table + "\n");
  writeFileSync(artifactFile, JSON.stringify(metrics, null, 2) + "\n");

  return { summary: table, artifact: artifactFile };
}

export default tool({
  description:
    "Emit loop-termination metrics (step count, token cost, convergence rate, failure mode) to GITHUB_STEP_SUMMARY and loop-metrics.json artifact.",
  args: {
    steps: tool.schema.number().int().min(0).describe("Loop step count"),
    progressDelta: tool.schema
      .number()
      .min(0)
      .describe("Progress delta for convergence rate"),
    failureMode: tool.schema
      .enum(["success", "max-steps", "error", "timeout", "unknown"])
      .optional()
      .describe("Failure mode: success, max-steps, error, timeout, unknown"),
    tokenEstimate: tool.schema
      .number()
      .optional()
      .describe("Estimated token cost per step"),
    summaryPath: tool.schema
      .string()
      .optional()
      .describe(
        "Path to append the summary table to (defaults to GITHUB_STEP_SUMMARY)",
      ),
    artifactPath: tool.schema
      .string()
      .optional()
      .describe(
        "Path to write the metrics JSON artifact to (defaults to loop-metrics.json)",
      ),
  },
  async execute(args, context) {
    const mode = (args.failureMode as LoopMetrics["failureMode"]) ?? "unknown";
    const metrics = computeMetrics(
      args.steps,
      args.progressDelta ?? 1,
      mode,
      args.tokenEstimate,
    );
    const result = emitMetrics(metrics, args.summaryPath, args.artifactPath);
    return `Metrics emitted: ${JSON.stringify(metrics)}\nSummary: ${result.summary}\nArtifact: ${result.artifact}`;
  },
});
