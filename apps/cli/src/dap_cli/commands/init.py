"""dap init — bootstrap ./.dap/ + admin user (#302 sub-D4)."""

from __future__ import annotations

import getpass
import json
import sys

from rich.console import Console

from dap_cli.bootstrap import (
    bootstrap_marker_path,
    ensure_admin_user,
    validate_email,
    validate_password,
    write_bootstrap_marker,
)
from dap_cli.paths import (
    DEFAULT_DASHBOARD_PORT,
    DEFAULT_ENGINE_PORT,
    local_config_path,
    local_dap_dir,
    local_db_path,
)

console = Console()


def init_command(
    force: bool = False,
    admin_email: str | None = None,
    admin_password: str | None = None,
    admin_password_stdin: bool = False,
) -> None:
    """Initialise ``.dap/`` and create / promote the bootstrap admin.

    Three input modes for the admin credentials, in order of
    precedence:

    1. ``--admin-email`` + ``--admin-password-stdin`` (read password
       from stdin) — automation-friendly, mirrors ``kubectl``.
    2. ``--admin-email`` + ``--admin-password=...`` — quick, suitable
       for local dev only (the password lands in shell history).
    3. Interactive prompt — if neither flag is set, prompt for both.
       Empty password → generate a random one and print once.

    Re-running is idempotent: an existing user with the supplied
    email gets promoted to admin without changing their password.
    """
    dap_dir = local_dap_dir()
    config_path = local_config_path()

    if dap_dir.exists() and not force:
        console.print(f"[yellow]⚠ .dap/ already exists at {dap_dir}[/yellow]")
        console.print("[dim]  Use --force to reinitialize.[/dim]")
        return

    (dap_dir / "pipelines").mkdir(parents=True, exist_ok=True)
    (dap_dir / "agents").mkdir(parents=True, exist_ok=True)
    (dap_dir / "runs").mkdir(parents=True, exist_ok=True)

    default_config = {
        "version": 1,
        "engine": {"host": "127.0.0.1", "port": DEFAULT_ENGINE_PORT},
        "dashboard": {"port": DEFAULT_DASHBOARD_PORT},
        "runtimes": {"paths": {}},
    }
    config_path.write_text(json.dumps(default_config, indent=2) + "\n", encoding="utf-8")

    # Bootstrap the admin user. Errors here are user-visible — we
    # leave the ``.dap/`` skeleton on disk (better than rolling back
    # to "no project at all" and forcing a re-init) but report the
    # admin step as failed so the operator can fix and retry.
    try:
        email, password = _collect_credentials(
            admin_email=admin_email,
            admin_password=admin_password,
            admin_password_stdin=admin_password_stdin,
        )
    except (ValueError, EOFError, KeyboardInterrupt) as exc:
        console.print(f"[red]✗ Admin bootstrap aborted: {exc}[/red]")
        console.print("[dim]  Re-run `dap init --force` to retry.[/dim]")
        raise SystemExit(2) from exc

    result = ensure_admin_user(
        local_db_path(),
        email=email,
        password=password,
    )
    write_bootstrap_marker(bootstrap_marker_path(dap_dir), result)

    console.print("[green]✓ Initialized DAP project[/green]")
    console.print(f"[dim]  {dap_dir}/[/dim]")
    console.print("[dim]  ├─ config.json[/dim]")
    console.print("[dim]  ├─ bootstrap.json[/dim]  [dim](admin metadata)[/dim]")
    console.print("[dim]  ├─ pipelines/[/dim]")
    console.print("[dim]  ├─ agents/[/dim]")
    console.print("[dim]  └─ runs/[/dim]")
    console.print()
    if result.promoted_existing:
        console.print(
            f"[green]✓ Admin: {result.email}[/green] [dim](existing user — promoted)[/dim]"
        )
    else:
        console.print(f"[green]✓ Admin: {result.email}[/green]")
    if result.generated_password is not None:
        # Print exactly once. We don't store it anywhere — the
        # operator must capture it now or run a reset later.
        console.print()
        console.print("[yellow]⚠ Generated random password — copy it now:[/yellow]")
        console.print(f"  [bold]{result.generated_password}[/bold]")
        console.print(
            "[dim]  This is the only time it will be shown. "
            "Re-run `dap init --force --admin-email=... --admin-password=...`[/dim]"
        )
        console.print("[dim]  to set your own.[/dim]")
    console.print()
    console.print("[cyan]Next: dap start[/cyan]")


def _collect_credentials(
    *,
    admin_email: str | None,
    admin_password: str | None,
    admin_password_stdin: bool,
) -> tuple[str, str | None]:
    """Resolve the email + password from flags or interactive prompts.

    Returns ``(email, password)`` where ``password`` may be ``None``
    when neither a flag nor an interactive entry produced one — in
    that case the bootstrap helper generates a random value.
    """
    # Email — flag wins; otherwise prompt. We never accept email
    # via stdin because mixing stdin email + stdin password would
    # be ambiguous, and emails are short enough to type live.
    if admin_email is None:
        if not sys.stdin.isatty():
            raise ValueError(
                "No --admin-email and stdin is not a TTY — can't prompt. "
                "Pass --admin-email=... explicitly for non-interactive runs."
            )
        admin_email = input("Admin email: ").strip()
    email = validate_email(admin_email)

    # Password — three paths.
    if admin_password_stdin:
        # Read the WHOLE stdin (operator pipes via ``echo ... | dap init``).
        # Strip trailing newline only — other whitespace is part of the
        # password, the operator picks the format.
        password: str | None = sys.stdin.read().rstrip("\n")
        if not password:
            raise ValueError("--admin-password-stdin set but stdin was empty")
        validate_password(password)
        return email, password

    if admin_password is not None:
        validate_password(admin_password)
        return email, admin_password

    if not sys.stdin.isatty():
        # Non-interactive with no password provided → generate one.
        # The bootstrap helper handles the generation; we just signal
        # by returning ``None``.
        return email, None

    # Interactive prompt with getpass (no echo).
    typed = getpass.getpass("Admin password (leave empty to auto-generate a random one): ")
    if typed == "":
        return email, None
    validate_password(typed)
    confirm = getpass.getpass("Re-enter password: ")
    if confirm != typed:
        raise ValueError("passwords did not match")
    return email, typed
