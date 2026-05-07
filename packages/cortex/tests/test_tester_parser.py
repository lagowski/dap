"""Tests for _parse_pytest_output — specifically the port-number artifact fix (#222)."""

import pytest
from cortex.nodes.tester import _parse_pytest_output

# ---------------------------------------------------------------------------
# Port-number artifact regression tests
# ---------------------------------------------------------------------------

_PSYCOPG3_ERROR = (
    'psycopg.OperationalError: connection to server at "10.0.0.5", '
    "port 30432 failed: Connection refused\n"
    "\tIs the server running on host and accepting TCP/IP connections?"
)

_PYTEST_INFRA_OUTPUT = f"""\
FAILED tests/test_audit.py::TestLogDecision::test_log_decision_inserts_row - {_PSYCOPG3_ERROR}
FAILED tests/test_audit.py::TestLogGitOp::test_log_git_op_inserts_row - {_PSYCOPG3_ERROR}
FAILED tests/test_audit.py::TestListDecisions::test_list_decisions_returns_all - {_PSYCOPG3_ERROR}
short test summary info
FAILED tests/test_audit.py::TestLogDecision::test_log_decision_inserts_row
FAILED tests/test_audit.py::TestLogGitOp::test_log_git_op_inserts_row
FAILED tests/test_audit.py::TestListDecisions::test_list_decisions_returns_all
============================== 3 failed, 1018 passed in 45.12s ==============================
"""


def test_port_number_not_parsed_as_failure_count() -> None:
    """The port 30432 in the psycopg3 error must NOT be returned as `failed`."""
    result = _parse_pytest_output(_PYTEST_INFRA_OUTPUT, returncode=1)
    assert result["failed"] == 3, (
        f"Expected 3 failures from summary line, got {result['failed']} — "
        "likely parsed port 30432 from connection-error message"
    )


def test_correct_passed_count_with_infra_errors() -> None:
    result = _parse_pytest_output(_PYTEST_INFRA_OUTPUT, returncode=1)
    assert result["passed"] == 1018


def test_failed_test_names_extracted() -> None:
    result = _parse_pytest_output(_PYTEST_INFRA_OUTPUT, returncode=1)
    # pytest lists FAILED names twice: once with error detail, once in summary section
    assert len(result["failed_tests"]) >= 3
    assert (
        "tests/test_audit.py::TestLogDecision::test_log_decision_inserts_row"
        in result["failed_tests"]
    )


# ---------------------------------------------------------------------------
# Normal pytest output (no infra errors)
# ---------------------------------------------------------------------------

_NORMAL_OUTPUT = """\
FAILED tests/test_cli.py::test_format_json - AssertionError: expected JSON
============================= 1 failed, 50 passed in 2.34s =============================
"""


def test_normal_failure_count() -> None:
    result = _parse_pytest_output(_NORMAL_OUTPUT, returncode=1)
    assert result["failed"] == 1
    assert result["passed"] == 50
    assert result["errors"] == 0


_ALL_PASS_OUTPUT = """\
============================= 200 passed in 3.11s =============================
"""


def test_all_pass_output() -> None:
    result = _parse_pytest_output(_ALL_PASS_OUTPUT, returncode=0)
    assert result["failed"] == 0
    assert result["passed"] == 200


_ERRORS_OUTPUT = """\
============================= 2 errors in 0.45s =============================
"""


def test_errors_only_output() -> None:
    result = _parse_pytest_output(_ERRORS_OUTPUT, returncode=1)
    assert result["errors"] == 2
    assert result["failed"] == 0


def test_non_zero_returncode_with_no_summary_sets_errors() -> None:
    """No pytest summary (e.g. import error before collection) → errors=1."""
    result = _parse_pytest_output("SyntaxError: invalid syntax\n", returncode=1)
    assert result["errors"] == 1
    assert result["passed"] == 0
    assert result["failed"] == 0


# ---------------------------------------------------------------------------
# Large numbers in error messages (general artifact guard)
# ---------------------------------------------------------------------------

_SOCKET_ERROR_OUTPUT = """\
FAILED tests/test_db.py::test_connect - OSError: [Errno 111] Connection refused
short test summary info
FAILED tests/test_db.py::test_connect
============================= 1 failed in 0.05s =============================
"""


def test_socket_error_count_not_bloated() -> None:
    """Socket error numbers like '111' must not pollute the failed count."""
    result = _parse_pytest_output(_SOCKET_ERROR_OUTPUT, returncode=1)
    assert result["failed"] == 1


@pytest.mark.parametrize(
    "port,expected_failed",
    [
        (5432, 2),
        (30432, 2),
        (27017, 2),
    ],
)
def test_various_db_ports_not_parsed_as_failures(port: int, expected_failed: int) -> None:
    output = (
        f"FAILED tests/test_a.py::test_one - OperationalError: port {port} failed: refused\n"
        f"FAILED tests/test_a.py::test_two - OperationalError: port {port} failed: refused\n"
        f"============================== {expected_failed} failed, 100 passed in 1.00s "
        "=============================="
    )
    result = _parse_pytest_output(output, returncode=1)
    assert result["failed"] == expected_failed
