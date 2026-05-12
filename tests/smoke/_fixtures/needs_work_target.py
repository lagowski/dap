# Smoke-run fixture for the needs_work → retry path (#174).
# code_reviewer rejects this file (debug print found); on retry the
# coder removes the print() and the second pass reaches gate-phase2.
#
# Lives under tests/smoke/_fixtures/ (the leading underscore keeps it
# off pytest's collection radar — function name "smoke_test" without
# the test_ prefix would be skipped anyway, but the directory layout
# also prevents accidental discovery of any future test_*.py here).


def smoke_test() -> str:
    print("debug output")  # intentional debug print — triggers code_reviewer rejection
    return "ok"
