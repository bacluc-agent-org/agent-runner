import unittest

from proof_5 import fork_slug


class ForkSlugTests(unittest.TestCase):
    def test_joins_owner_and_repository_with_a_hyphen(self):
        self.assertEqual(fork_slug("bacluc-agent", "agent-runner"), "bacluc-agent-agent-runner")
        self.assertEqual(fork_slug("octocat", "hello-world"), "octocat-hello-world")
        self.assertEqual(fork_slug("Owner", "Repo"), "Owner-Repo")


if __name__ == "__main__":
    unittest.main()
