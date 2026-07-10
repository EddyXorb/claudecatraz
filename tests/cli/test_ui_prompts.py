"""Tests for Out.ask / Out.choice / Out.secret interaction helpers."""

import pytest

from catraz.ui import Out


@pytest.fixture
def out() -> Out:
    return Out(color=False)


# ask


class TestAsk:
    def test_returns_typed_value(self, out: Out, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("builtins.input", lambda prompt: "typed")
        assert out.ask("Enter value", default="fallback") == "typed"

    def test_returns_default_on_empty_input(
        self, out: Out, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("builtins.input", lambda prompt: "")
        assert out.ask("Enter value", default="fallback") == "fallback"

    def test_returns_default_on_eof(self, out: Out, monkeypatch: pytest.MonkeyPatch) -> None:
        def raise_eof(prompt: str) -> None:
            raise EOFError

        monkeypatch.setattr("builtins.input", raise_eof)
        assert out.ask("Enter value", default="fallback") == "fallback"

    def test_returns_empty_string_when_no_default_and_empty_input(
        self, out: Out, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("builtins.input", lambda prompt: "")
        assert out.ask("Enter value") == ""

    def test_returns_empty_string_when_no_default_and_eof(
        self, out: Out, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def raise_eof(prompt: str) -> None:
            raise EOFError

        monkeypatch.setattr("builtins.input", raise_eof)
        assert out.ask("Enter value") == ""


# choice


class TestChoice:
    OPTIONS = [
        ("off", "No GitLab"),
        ("read-only", "Read only"),
        ("read-write", "Read + write"),
    ]

    def test_returns_default_on_empty_input(
        self, out: Out, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("builtins.input", lambda prompt: "")
        result = out.choice("Pick mode", self.OPTIONS, default=1)
        assert result == "read-only"

    def test_returns_selected_value_for_valid_number(
        self, out: Out, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("builtins.input", lambda prompt: "3")
        result = out.choice("Pick mode", self.OPTIONS, default=0)
        assert result == "read-write"

    def test_terminates_and_returns_default_after_3_junk_inputs(
        self, out: Out, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Proves the retry loop is bounded: constant non-empty junk never loops forever."""
        call_count: int = 0

        def always_junk(prompt: str) -> str:
            nonlocal call_count
            call_count += 1
            return "garbage"

        monkeypatch.setattr("builtins.input", always_junk)
        result = out.choice("Pick mode", self.OPTIONS, default=0)
        assert result == "off"
        # 3 tries inside choice, each calls ask once which calls input once
        assert call_count == 3

    def test_returns_default_on_eof(self, out: Out, monkeypatch: pytest.MonkeyPatch) -> None:
        def raise_eof(prompt: str) -> None:
            raise EOFError

        monkeypatch.setattr("builtins.input", raise_eof)
        result = out.choice("Pick mode", self.OPTIONS, default=2)
        assert result == "read-write"


# secret


class TestSecret:
    def test_returns_typed_secret(self, out: Out, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("getpass.getpass", lambda prompt: "s3cr3t")
        assert out.secret("Token") == "s3cr3t"

    def test_returns_current_on_empty_input(
        self, out: Out, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("getpass.getpass", lambda prompt: "")
        assert out.secret("Token", current="existing") == "existing"

    def test_returns_current_on_eof(self, out: Out, monkeypatch: pytest.MonkeyPatch) -> None:
        def raise_eof(prompt: str) -> None:
            raise EOFError

        monkeypatch.setattr("getpass.getpass", raise_eof)
        assert out.secret("Token", current="existing") == "existing"

    def test_returns_empty_when_no_current_and_empty_input(
        self, out: Out, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("getpass.getpass", lambda prompt: "")
        assert out.secret("Token") == ""

    def test_returns_typed_value_over_current(
        self, out: Out, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("getpass.getpass", lambda prompt: "newtoken")
        assert out.secret("Token", current="oldtoken") == "newtoken"
