"""P3: `up` removed; `down` renamed to `stop` with `down` kept as a back-compat alias."""
import pytest

from catraz import cli
from catraz.cli import build_parser
from catraz.commands import stack


def test_stop_command_token():
    assert build_parser().parse_args(["stop"]).command == "stop"


def test_down_alias_token():
    # argparse reports the literal token invoked, so the alias stays distinguishable.
    assert build_parser().parse_args(["down"]).command == "down"


def test_both_handlers_route_to_cmd_down():
    assert cli.HANDLERS["stop"] is stack.cmd_down
    assert cli.HANDLERS["down"] is stack.cmd_down


def test_stop_keeps_volumes_flag():
    assert build_parser().parse_args(["stop", "-v"]).volumes is True


def test_stop_keeps_print_flag():
    assert build_parser().parse_args(["stop", "--print"]).print_only is True


def test_up_command_removed():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["up"])
