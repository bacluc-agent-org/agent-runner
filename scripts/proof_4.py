import unittest


def retry_call(fn, attempts: int = 3):
    if attempts < 1:
        raise ValueError("attempts must be at least 1")

    for attempt in range(attempts):
        try:
            return fn()
        except Exception:
            if attempt == attempts - 1:
                raise


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


if __name__ == "__main__":
    unittest.main()
