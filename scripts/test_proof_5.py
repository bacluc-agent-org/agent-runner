import unittest

from proof_5 import fork_slug


class ForkSlugTests(unittest.TestCase):
    def test_owned_repositories_keep_their_owner(self):
        self.assertEqual(fork_slug("BacLuc", "repo"), "BacLuc/repo")
        self.assertEqual(fork_slug("bacluc-agent", "repo"), "bacluc-agent/repo")
        self.assertEqual(fork_slug("bacluc-agent-org", "repo"), "bacluc-agent-org/repo")

    def test_outsider_uses_bacluc_agent_fork(self):
        self.assertEqual(fork_slug("outsider", "repo"), "bacluc-agent/repo")
