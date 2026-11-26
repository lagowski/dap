"""dap pipeline — offline operations on pipeline bundle files."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from dap_cli.hub_export import write_hub_bundle

pipeline_app = typer.Typer(
    name="pipeline",
    help="Offline operations on pipeline bundle files",
    no_args_is_help=True,
    add_completion=False,
)


@pipeline_app.command("export")
def cmd_export(
    bundle: Annotated[
        Path, typer.Argument(help="A *.pipeline-bundle.json file", exists=True, dir_okay=False)
    ],
    hub: Annotated[
        str, typer.Option("--hub", help="Hub page slug: writes a Labs hub pipeline bundle")
    ],
    tagline5: Annotated[
        str,
        typer.Option(
            "--tagline5", help="Hub card line: 3-6 words, at most 60 characters, plain text"
        ),
    ],
    io: Annotated[
        str,
        typer.Option(
            "--io",
            help="Hub card icons <in>:<out>, from: bulb scale repo issue merge docs post "
            "search report script clapperboard blocks pipeline list fanout comment",
        ),
    ],
    out: Annotated[
        Path, typer.Option("--out", help="Directory that receives <slug>/page/page.json")
    ] = Path("."),
) -> None:
    """Write a hub tutorial bundle (with its read-only graph) for one pipeline bundle."""
    try:
        target = write_hub_bundle(bundle, hub, out, tagline5=tagline5, io=io)
    except ValueError as exc:  # Unsafe is a ValueError: a refusal, a bad slug, tagline or icon
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    typer.echo(f"wrote {target}")
