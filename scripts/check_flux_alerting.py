"""Validate that Flux failures use durable state instead of event email loops."""

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
FLUX_NOTIFICATIONS = (
    ROOT / "infrastructure/observability-config/flux-notifications.yaml"
)
PLATFORM_ALERTS = (
    ROOT / "infrastructure/observability-config/alerts/platform-alerts.yaml"
)
PROMETHEUS_STACK = ROOT / "infrastructure/observability/kube-prometheus-stack.yaml"
OBSERVABILITY_CONFIG = ROOT / "clusters/sisyphus/observability-config.yaml"
STATEFUL_KINDS = {"Kustomization", "HelmRelease"}


def _resource(documents: list[dict[str, Any]], kind: str, name: str) -> dict[str, Any]:
    return next(
        document
        for document in documents
        if document.get("kind") == kind
        and document.get("metadata", {}).get("name") == name
    )


def validate_documents(
    notifications: list[dict[str, Any]],
    platform_alerts: dict[str, Any],
    prometheus_stack: dict[str, Any],
    observability_config: dict[str, Any],
) -> None:
    """Reject event-driven paging for resources covered by stateful metrics."""

    event_alert = _resource(notifications, "Alert", "reconciliation-errors")
    event_kinds = {source["kind"] for source in event_alert["spec"]["eventSources"]}
    duplicate_kinds = event_kinds & STATEFUL_KINDS
    if duplicate_kinds:
        raise ValueError(
            "event email alert must exclude statefully monitored resources: "
            + ", ".join(sorted(duplicate_kinds))
        )

    dependencies = {
        dependency["name"]
        for dependency in observability_config["spec"].get("dependsOn", [])
    }
    if "litellm" in dependencies:
        raise ValueError("observability-config must not depend on LiteLLM")

    rules = platform_alerts["spec"]["groups"][0]["rules"]
    flux_rule = next(rule for rule in rules if rule["alert"] == "FluxReconciliationStalled")
    expression = flux_rule["expr"]
    if "gotk_resource_info" not in expression or 'ready="False"' not in expression:
        raise ValueError("Flux reconciliation alert must use durable resource state")

    state_config = prometheus_stack["spec"]["values"]["kube-state-metrics"][
        "customResourceState"
    ]
    if not state_config.get("enabled"):
        raise ValueError("kube-state-metrics custom resource state must be enabled")
    resources = state_config["config"]["spec"]["resources"]
    configured_kinds = {
        resource["groupVersionKind"]["kind"]
        for resource in resources
        if resource.get("metricNamePrefix") == "gotk"
    }
    missing_kinds = STATEFUL_KINDS - configured_kinds
    if missing_kinds:
        raise ValueError(
            "missing Flux state metrics for: " + ", ".join(sorted(missing_kinds))
        )


def main() -> None:
    notifications = list(yaml.safe_load_all(FLUX_NOTIFICATIONS.read_text()))
    platform_alerts = yaml.safe_load(PLATFORM_ALERTS.read_text())
    prometheus_stack = yaml.safe_load(PROMETHEUS_STACK.read_text())
    observability_config = yaml.safe_load(OBSERVABILITY_CONFIG.read_text())
    try:
        validate_documents(
            notifications,
            platform_alerts,
            prometheus_stack,
            observability_config,
        )
    except (KeyError, StopIteration, TypeError, ValueError) as exc:
        raise SystemExit(f"Flux alerting check failed: {exc}") from exc
    print("Flux alerting uses durable reconciliation state")


if __name__ == "__main__":
    main()
