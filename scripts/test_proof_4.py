import unittest

from proof_4 import retry_call


class RetryCallTests(unittest.TestCase):
    def test_returns_successful_result(self):
        self.assertEqual(retry_call(lambda: "done"), "done")

    def test_retries_flaky_call_until_it_succeeds(self):
        calls = 0

        def flaky_call():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("try again")
            return "recovered"

        self.assertEqual(retry_call(flaky_call), "recovered")
        self.assertEqual(calls, 2)

    def test_raises_after_final_attempt(self):
        calls = 0

        def failing_call():
            nonlocal calls
            calls += 1
            raise RuntimeError("failed")

        with self.assertRaisesRegex(RuntimeError, "failed"):
            retry_call(failing_call, attempts=2)
        self.assertEqual(calls, 2)
