"""Test doubles shared across test files."""
from types import SimpleNamespace


class FakeGroq:
    """Mimics the Groq client surface: returns canned replies in order and counts calls."""

    def __init__(self, replies, fail_with: Exception | None = None):
        self.replies = list(replies)
        self.fail_with = fail_with
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        assert kwargs["temperature"] == 0.0 and kwargs["seed"] == 42
        self.calls.append(kwargs)
        if self.fail_with:
            raise self.fail_with
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.replies.pop(0)))],
            usage=SimpleNamespace(prompt_tokens=300, completion_tokens=60),
        )
