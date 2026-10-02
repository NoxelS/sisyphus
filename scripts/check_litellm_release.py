"""Validate the LiteLLM release and its single-node upgrade envelope."""

import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "infrastructure/litellm/litellm-ocirepository.yaml"
RELEASE = ROOT / "infrastructure/litellm/helmrelease.yaml"
KUSTOMIZATION = ROOT / "clusters/sisyphus/litellm.yaml"
UPGRADE_MEMORY_BUDGET_BYTES = 4 * 1024**3
MIGRATION_STARTUP_REQUEST_LIMIT_BYTES = 1024**3


def _memory_bytes(value: str) -> int:
    match = re.fullmatch(r"(\d+)(Ki|Mi|Gi)", value)
    if match is None:
        raise ValueError(f"unsupported memory quantity {value!r}")
    multipliers = {"Ki": 1024, "Mi": 1024**2, "Gi": 1024**3}
    return int(match.group(1)) * multipliers[match.group(2)]


def _duration_seconds(value: str) -> int:
    match = re.fullmatch(r"(\d+)(s|m|h)", value)
    if match is None:
        raise ValueError(f"unsupported duration {value!r}")
    multipliers = {"s": 1, "m": 60, "h": 3600}
    return int(match.group(1)) * multipliers[match.group(2)]


def validate_documents(
    chart: dict[str, Any],
    release: dict[str, Any],
    kustomization: dict[str, Any],
) -> None:
    """Reject releases that cannot safely run their hook on the single node."""

    chart_tag = chart["spec"]["ref"]["tag"]
    image_tag = release["spec"]["values"]["image"]["tag"]
    image_version = re.fullmatch(r"v(\d+\.\d+\.\d+)@sha256:[0-9a-f]{64}", image_tag)
    if image_version is None or chart_tag != image_version.group(1):
        raise ValueError(
            f"LiteLLM chart {chart_tag} and pinned image {image_tag} must use "
            "the same release version"
        )

    values = release["spec"]["values"]
    runtime_request = _memory_bytes(values["resources"]["requests"]["memory"])
    migration = values["migrationJob"]
    migration_request = _memory_bytes(
        migration["resources"]["requests"]["memory"]
    )
    if migration_request > MIGRATION_STARTUP_REQUEST_LIMIT_BYTES:
        raise ValueError(
            "LiteLLM migration memory request exceeds the 1Gi bootstrap "
            "headroom available before a pre-upgrade hook"
        )
    combined_request = runtime_request + migration_request
    if combined_request > UPGRADE_MEMORY_BUDGET_BYTES:
        raise ValueError(
            "LiteLLM runtime and migration memory requests exceed the 4Gi "
            "single-node upgrade budget"
        )

    helm_timeout = _duration_seconds(release["spec"]["timeout"])
    migration_deadline = int(migration["activeDeadlineSeconds"])
    if helm_timeout < migration_deadline + 60:
        raise ValueError(
            "LiteLLM Helm timeout must exceed the migration deadline by at "
            "least 60 seconds"
        )

    reconciliation_timeout = _duration_seconds(kustomization["spec"]["timeout"])
    if reconciliation_timeout < helm_timeout + 60:
        raise ValueError(
            "LiteLLM Kustomization timeout must exceed the Helm timeout by at "
            "least 60 seconds"
        )


def main() -> None:
    chart = yaml.safe_load(CHART.read_text())
    release = yaml.safe_load(RELEASE.read_text())
    kustomization = yaml.safe_load(KUSTOMIZATION.read_text())
    try:
        validate_documents(chart, release, kustomization)
    except (KeyError, TypeError, ValueError) as exc:
        raise SystemExit(f"LiteLLM deployment check failed: {exc}") from exc
    print("LiteLLM deployment is safe for single-node upgrades")


if __name__ == "__main__":
    main()
