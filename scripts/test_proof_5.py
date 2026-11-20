import unittest

from proof_5 import fork_slug


class ForkSlugTests(unittest.TestCase):
    def test_uses_fork_for_owners_outside_the_bac_luc_organizations(self):
        self.assertEqual(fork_slug("octocat", "hello-world"), "bacluc-agent/hello-world")
        self.assertEqual(fork_slug("Owner", "Repo"), "bacluc-agent/Repo")

    def test_preserves_repository_for_bac_luc_organizations(self):
        self.assertEqual(fork_slug("BacLuc", "agent-runner"), "BacLuc/agent-runner")
        self.assertEqual(fork_slug("bacluc-agent", "agent-runner"), "bacluc-agent/agent-runner")
        self.assertEqual(fork_slug("bacluc-agent-org", "agent-runner"), "bacluc-agent-org/agent-runner")


if __name__ == "__main__":
    unittest.main()
