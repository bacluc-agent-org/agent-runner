import os
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("run_issue_selector.sh")
WORKFLOW = SCRIPT.parents[1] / ".github/workflows/hourly-issue.yml"


class IssueSelectorRetryTests(unittest.TestCase):
    def run_selector(self, responses, models=("m1", "m2")):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            response_file = root / "responses"
            response_file.write_text("\n".join(responses))
            calls_file = root / "calls"
            fake = bin_dir / "opencode"
            fake.write_text(
                "#!/usr/bin/env bash\n"
                "n=0; [[ -f \"$CALLS\" ]] && n=$(wc -l < \"$CALLS\")\n"
                "printf '%s|%s\\n' \"$5\" \"$8\" >> \"$CALLS\"\n"
                "response=$(sed -n \"$((n+1))p\" \"$RESPONSES\")\n"
                "printf '%s\\n' \"${response%|*}\"\n"
                "exit \"${response##*|}\"\n"
            )
            fake.chmod(0o755)
            prompt, output, candidates, fatal = [root / name for name in ("prompt", "out", "candidates", "fatal")]
            prompt.write_text("same prompt\n")
            candidates.write_text("7\n")
            env = os.environ | {"PATH": f"{bin_dir}:{os.environ['PATH']}", "CALLS": str(calls_file), "RESPONSES": str(response_file)}
            result = subprocess.run(["bash", str(SCRIPT), str(prompt), str(output), str(candidates), str(fatal), "1", *models], env=env, text=True, capture_output=True)
            calls = calls_file.read_text().splitlines() if calls_file.exists() else []
            contents = output.read_text() if output.exists() else ""
            selected_model = Path(f"{output}.model").read_text().strip() if Path(f"{output}.model").exists() else ""
            return result, contents, selected_model, calls

    def test_retries_once_with_same_prompt_and_isolates_output(self):
        result, output, selected_model, calls = self.run_selector(["partial|1", "SELECTED_ISSUE: 7|0"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0].split("|", 1)[1], calls[1].split("|", 1)[1])
        self.assertEqual(output, "SELECTED_ISSUE: 7\n")
        self.assertEqual(selected_model, "m2")

    def test_no_alternate_and_both_fail(self):
        result, _, _, calls = self.run_selector(["failure|1"], models=("m1",))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(calls), 1)
        result, _, _, calls = self.run_selector(["failure|1", "failure|1"])
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(calls), 2)

    def test_no_available_model(self):
        result, _, _, calls = self.run_selector([], models=())
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, [])

    def test_success_with_malformed_output_does_not_retry(self):
        result, _, _, calls = self.run_selector(["malformed|0", "SELECTED_ISSUE: 7|0"])
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(calls), 1)

    def test_workflow_claims_only_after_selector_validation(self):
        workflow = WORKFLOW.read_text()
        self.assertEqual(workflow.count("gh issue edit \"$issue_number\""), 1)
        self.assertLess(workflow.index("scripts/run_issue_selector.sh"), workflow.index("gh issue edit \"$issue_number\""))


if __name__ == "__main__":
    unittest.main()
