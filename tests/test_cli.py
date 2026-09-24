import io
from pathlib import Path

import pytest

from app import cli
from app.config import load_config
from app.container import build_container

TEST_CONFIG = Path(__file__).with_name("config.test.toml")
PICKUP = ("--lat", "12.9716", "--lng", "77.5946")
TEN_KM_NORTH = ("--lat", str(12.9716 + 10 / 111.19492664455873), "--lng", "77.5946")


@pytest.fixture
def c():
    return build_container(load_config(TEST_CONFIG))


def call(c, *argv: str) -> str:
    return cli.run(c, cli.build_parser().parse_args(argv))


def test_full_ride_through_the_cli(c):
    user = call(c, "register-user", "--name", "Asha", "--phone", "900").split()[2]
    call(c, "register-driver", "--name", "Sam", "--phone", "911", "--car-type", "sedan", *PICKUP)
    call(c, "add-coupon", "flat10", "--type", "flat", "--value", "10")

    booked = call(c, "book", "--user", user, "--car-type", "hatchback", "--coupon", "FLAT10", *PICKUP)
    assert "car=sedan (upgraded from hatchback, billed as hatchback)" in booked
    ride_id = booked.split()[2]
    assert "[ongoing]" in call(c, "start", ride_id)

    ended = call(c, "end", ride_id, *TEN_KM_NORTH)
    assert "distance 10.00 km  base fare ₹69.00" in ended
    assert "coupon discount -₹10.00" in ended
    assert "total ₹59.00" in ended
    assert "COMPLETED (1)" in call(c, "user-rides", user)


@pytest.mark.parametrize("argv, message", [
    (["register-driver", "--name", "S", "--phone", "1", "--car-type", "sedan", "--lat", "200", "--lng", "0"],
     "invalid coordinates"),
    (["add-coupon", "X", "--type", "flat", "--value", "10", "--max-discount", "5"], "does not take: max_discount"),
    (["book", "--user", "U-missing", "--car-type", "sedan", *PICKUP], "user 'U-missing' not found"),
])
def test_domain_errors_are_reported_with_exit_code_1(c, capsys, argv, message):
    assert cli.execute(c, cli.build_parser().parse_args(argv)) == 1
    assert message in capsys.readouterr().err


def test_memory_shell_keeps_state_and_survives_bad_input(monkeypatch, capsys):
    monkeypatch.setenv("RIDES_CONFIG", str(TEST_CONFIG))
    monkeypatch.setattr("sys.stdin", io.StringIO(
        "register-user --name A --phone 1\n"
        "register-user --name B --phone 1\n"      # fails only if the first user is still there
        "book --bogus\n"
        "# a comment\n"
        "exit\n"
        "register-user --name never --phone 2\n"))
    assert cli.main(["shell", "--memory"]) == 0
    out, err = capsys.readouterr()
    assert "registered user" in out and "never" not in out
    assert "phone 1 is already registered" in err
    assert "the following arguments are required" in err


def test_bad_config_exits_with_code_2(monkeypatch, tmp_path, capsys):
    bad = tmp_path / "bad.toml"
    bad.write_text("[booking\n")
    monkeypatch.setenv("RIDES_CONFIG", str(bad))
    assert cli.main(["shell", "--memory"]) == 2
    assert "config error" in capsys.readouterr().err
