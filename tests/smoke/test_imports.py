"""Smoke test — wszystkie pakiety workspace musi się dać zaimportować w czystym env."""

from __future__ import annotations


def test_dap_types_importable() -> None:
    import dap_types

    expected = {
        "Agent",
        "Pipeline",
        "PipelineNode",
        "PipelineEdge",
        "ComparisonCondition",
        "LogicalCondition",
        "Run",
        "NodeExecutionLog",
        "PipelineState",
        "StateSnapshot",
        "RuntimeAdapter",
        "RuntimeTask",
        "RuntimeResult",
        "HealthStatus",
    }
    actual = set(dir(dap_types))
    missing = expected - actual
    assert not missing, f"Missing exports from dap_types: {missing}"


def test_dap_runtimes_importable() -> None:
    from dap_runtimes import (
        AiderAdapter,
        ApiCallAdapter,
        BashAdapter,
        ClaudeCodeAdapter,
        CodexAdapter,
        GeminiCliAdapter,
        HttpAdapter,
        RuntimeRegistry,
        create_default_registry,
    )

    # Sanity check — adapter classes mają wymagane atrybuty.
    # Iterujemy po instancjach (nie po typach), żeby uniknąć zwężenia mypy
    # do abstract type[BaseAdapter].
    instances = [
        BashAdapter(),
        HttpAdapter(),
        ApiCallAdapter(),
        ClaudeCodeAdapter(),
        GeminiCliAdapter(),
        CodexAdapter(),
        AiderAdapter(),
    ]
    for instance in instances:
        assert isinstance(instance.id, str) and instance.id
        assert isinstance(instance.display_name, str) and instance.display_name
        assert instance.kind in {"cli", "api", "shell", "http"}

    # Registry działa
    registry = create_default_registry()
    assert isinstance(registry, RuntimeRegistry)
    assert len(registry.list()) == 7


def test_dap_engine_importable() -> None:
    from dap_engine import create_app
    from dap_engine.app import EngineConfig

    assert callable(create_app)
    assert EngineConfig().port == 7333


def test_dap_cli_importable() -> None:
    from dap_cli import __version__
    from dap_cli.__main__ import app

    assert __version__ == "0.0.1"
    # Typer app powinno mieć zarejestrowane komendy
    command_names = {cmd.name for cmd in app.registered_commands}
    assert {"init", "start", "stop", "status"} <= command_names
