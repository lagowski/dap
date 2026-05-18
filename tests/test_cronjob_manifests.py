"""Tests for k8s CronJob manifests in k8s/cronjobs/."""

from pathlib import Path
from typing import Any, cast

import pytest
import yaml  # type: ignore[import-untyped]

CRONJOBS_DIR = Path(__file__).parent.parent / "k8s" / "cronjobs"

Manifest = dict[str, Any]

MANIFEST_FILES: list[str] = [
    "cfd-weekly-sweep.yaml",
    "cfd-daily-watchlist.yaml",
    "cfd-earnings-sweep.yaml",
]


def _load_manifest(filename: str) -> Manifest:
    path = CRONJOBS_DIR / filename
    return cast(Manifest, yaml.safe_load(path.read_text()))


def _get_container(manifest: Manifest) -> Manifest:
    pod_spec = manifest["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    return cast(Manifest, pod_spec["containers"][0])


def _get_pod_spec(manifest: Manifest) -> Manifest:
    return cast(Manifest, manifest["spec"]["jobTemplate"]["spec"]["template"]["spec"])


@pytest.fixture(params=MANIFEST_FILES)
def manifest(request: pytest.FixtureRequest) -> Manifest:
    return _load_manifest(request.param)


@pytest.fixture(params=MANIFEST_FILES)
def manifest_with_name(request: pytest.FixtureRequest) -> tuple[str, Manifest]:
    return request.param, _load_manifest(request.param)


class TestCommonProperties:
    """Tests that apply to all three CronJob manifests."""

    @pytest.mark.parametrize("filename", MANIFEST_FILES)
    def test_api_version(self, filename: str) -> None:
        manifest = _load_manifest(filename)
        assert manifest["apiVersion"] == "batch/v1"

    @pytest.mark.parametrize("filename", MANIFEST_FILES)
    def test_kind_is_cronjob(self, filename: str) -> None:
        manifest = _load_manifest(filename)
        assert manifest["kind"] == "CronJob"

    @pytest.mark.parametrize("filename", MANIFEST_FILES)
    def test_namespace_is_trading(self, filename: str) -> None:
        manifest = _load_manifest(filename)
        assert manifest["metadata"]["namespace"] == "trading"

    @pytest.mark.parametrize("filename", MANIFEST_FILES)
    def test_concurrency_policy_forbid(self, filename: str) -> None:
        manifest = _load_manifest(filename)
        assert manifest["spec"]["concurrencyPolicy"] == "Forbid"

    @pytest.mark.parametrize("filename", MANIFEST_FILES)
    def test_restart_policy_on_failure(self, filename: str) -> None:
        manifest = _load_manifest(filename)
        pod_spec = _get_pod_spec(manifest)
        assert pod_spec["restartPolicy"] == "OnFailure"

    @pytest.mark.parametrize("filename", MANIFEST_FILES)
    def test_image_references_cfd_docker_image(self, filename: str) -> None:
        manifest = _load_manifest(filename)
        container = _get_container(manifest)
        assert container["image"] == "ghcr.io/dixter999/cfd-research:latest"

    @pytest.mark.parametrize("filename", MANIFEST_FILES)
    def test_image_pull_secrets_ghcr_pull_secret(self, filename: str) -> None:
        manifest = _load_manifest(filename)
        pod_spec = _get_pod_spec(manifest)
        assert pod_spec["imagePullSecrets"] == [{"name": "ghcr-pull-secret"}]

    @pytest.mark.parametrize("filename", MANIFEST_FILES)
    def test_env_from_cfd_research_env_secret(self, filename: str) -> None:
        manifest = _load_manifest(filename)
        container = _get_container(manifest)
        assert {"secretRef": {"name": "cfd-research-env"}} in container["envFrom"]

    @pytest.mark.parametrize("filename", MANIFEST_FILES)
    def test_no_inline_env_vars(self, filename: str) -> None:
        manifest = _load_manifest(filename)
        container = _get_container(manifest)
        assert container.get("env") is None

    @pytest.mark.parametrize("filename", MANIFEST_FILES)
    def test_resource_limits_memory_1gi_cpu_500m(self, filename: str) -> None:
        manifest = _load_manifest(filename)
        container = _get_container(manifest)
        limits = container["resources"]["limits"]
        assert limits["memory"] == "1Gi"
        assert limits["cpu"] == "500m"

    @pytest.mark.parametrize("filename", MANIFEST_FILES)
    def test_resource_requests(self, filename: str) -> None:
        manifest = _load_manifest(filename)
        container = _get_container(manifest)
        requests = container["resources"]["requests"]
        assert requests["memory"] == "512Mi"
        assert requests["cpu"] == "250m"


class TestWeeklySweep:
    def test_weekly_sweep_schedule_saturday_0200_utc(self) -> None:
        manifest = _load_manifest("cfd-weekly-sweep.yaml")
        assert manifest["spec"]["schedule"] == "0 2 * * 6"

    def test_weekly_sweep_args_cfd_sweep_all_cfd(self) -> None:
        manifest = _load_manifest("cfd-weekly-sweep.yaml")
        container = _get_container(manifest)
        assert container["args"] == ["cfd.sweep", "--watchlist", "all-cfd"]


class TestDailyWatchlist:
    def test_daily_watchlist_schedule_weekdays_0600_utc(self) -> None:
        manifest = _load_manifest("cfd-daily-watchlist.yaml")
        assert manifest["spec"]["schedule"] == "0 6 * * 1-5"

    def test_daily_watchlist_args_cfd_sweep_daily(self) -> None:
        manifest = _load_manifest("cfd-daily-watchlist.yaml")
        container = _get_container(manifest)
        assert container["args"] == ["cfd.sweep", "--watchlist", "daily"]


class TestEarningsSweep:
    def test_earnings_sweep_schedule_sunday_2000_utc(self) -> None:
        manifest = _load_manifest("cfd-earnings-sweep.yaml")
        assert manifest["spec"]["schedule"] == "0 20 * * 0"

    def test_earnings_sweep_args_cfd_sweep_earnings_week(self) -> None:
        manifest = _load_manifest("cfd-earnings-sweep.yaml")
        container = _get_container(manifest)
        assert container["args"] == ["cfd.sweep", "--watchlist", "earnings-week"]
