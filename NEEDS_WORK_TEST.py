# Smoke-run fixture for the needs_work → retry path (#174).
# code_reviewer rejects this file (debug print found); on retry the
# coder removes the print() and the second pass reaches gate-phase2.
# Intentionally not under apps/ or packages/ — it's a pipeline target,
# not engine source. Excluded from CI lint scope (apps + packages only).


def smoke_test():
    print("debug output")  # noqa: T201 — intentional, triggers code_reviewer rejection
    return "ok"
