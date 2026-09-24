import io

import pytest

from app.cli import main as cli
from app.container import build_container
from tests.support import PICKUP, TEST_CONFIG, TEST_CONFIG_PATH, north_of

AT_PICKUP = ("--lat", str(PICKUP.lat), "--lng", str(PICKUP.lng))
TEN_KM_NORTH = ("--lat", str(north_of(10).lat), "--lng", str(PICKUP.lng))


@pytest.fixture
def c():
    """The CLI's logic doesn't depend on storage, so these run in memory only."""
    return build_container(TEST_CONFIG)


def call(c, *argv: str) -> str:
    return cli.run(c, cli.build_parser().parse_args(argv))


def test_full_ride_through_the_cli(c):
    user = call(c, "register-user", "--name", "Asha", "--phone", "900").split()[2]
    call(c, "register-driver", "--name", "Sam", "--phone", "911", "--car-type", "sedan", *AT_PICKUP)
    call(c, "add-coupon", "flat10", "--type", "flat", "--value", "10")

    booked = call(c, "book", "--user", user, "--car-type", "hatchback", "--coupon", "FLAT10", *AT_PICKUP)
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
    (["book", "--user", "U-missing", "--car-type", "sedan", *AT_PICKUP], "user 'U-missing' not found"),
])
def test_domain_errors_are_reported_with_exit_code_1(c, capsys, argv, message):
    assert cli.execute(c, cli.build_parser().parse_args(argv)) == 1
    assert message in capsys.readouterr().err


def test_memory_shell_keeps_state_and_survives_bad_input(monkeypatch, capsys):
    monkeypatch.setenv("RIDES_CONFIG", str(TEST_CONFIG_PATH))
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
