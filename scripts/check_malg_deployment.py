"""Validate MALG image-driven deployment manifests."""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any

import yaml

_IMAGE = re.compile(
    r"^(?P<repo>ghcr\.io/noxels/malg(?:-frontend)?):"
    r"(?P<tag>v[0-9]+\.[0-9]+\.[0-9]+)@sha256:(?P<digest>[0-9a-f]{64})$"
)
_BACKEND_REPO = "ghcr.io/noxels/malg"
_FRONTEND_REPO = "ghcr.io/noxels/malg-frontend"


def _documents(root: Path) -> list[dict[str, Any]]:
    paths = [
        root / "infrastructure/malg/api-deployment.yaml",
        root / "infrastructure/malg/worker-deployment.yaml",
        root / "infrastructure/malg/crm-schema/job.yaml",
        root / "infrastructure/malg/migration-job.yaml",
        root / "infrastructure/malg/frontend-deployment.yaml",
        root / "infrastructure/malg/network-policy.yaml",
        root / "clusters/sisyphus/malg.yaml",
        root / "clusters/sisyphus/malg-crm-schema.yaml",
    ]
    docs: list[dict[str, Any]] = []
    for path in paths:
        if not path.is_file():
            raise ValueError(f"missing deployment manifest: {path}")
        for document in yaml.safe_load_all(path.read_text(encoding="utf-8")):
            if isinstance(document, dict):
                docs.append(document)
    return docs


def _template(document: dict[str, Any]) -> dict[str, Any]:
    template = document.get("spec", {}).get("template")
    if not isinstance(template, dict):
        raise ValueError(f"{document.get('metadata', {}).get('name')} has no pod template")
    return template


def _containers(document: dict[str, Any]) -> list[dict[str, Any]]:
    pod = _template(document).get("spec", {})
    containers = list(pod.get("containers", [])) + list(pod.get("initContainers", []))
    if not containers or not all(isinstance(c, dict) for c in containers):
        raise ValueError(f"{document.get('metadata', {}).get('name')} has no containers")
    return containers


def _workload(documents: list[dict[str, Any]], kind: str, name: str) -> dict[str, Any]:
    for document in documents:
        if document.get("kind") == kind and document.get("metadata", {}).get("name") == name:
            return document
    raise ValueError(f"missing {name} {kind}")


def _validate_image(value: Any, repository: str, label: str) -> tuple[str, str]:
    if not isinstance(value, str):
        raise ValueError(f"{label} image is missing")
    match = _IMAGE.fullmatch(value)
    if match is None or match.group("repo") != repository:
        raise ValueError(f"{label} must be a digest-pinned {repository} vX.Y.Z image")
    return match.group("tag"), value


def check(root: Path) -> None:
    if (root / "infrastructure/malg/release.json").exists():
        raise ValueError("release metadata is not permitted")
    if (root / "scripts/sync_malg_release.py").exists():
        raise ValueError("release synchronizer is not permitted")
    documents = _documents(root)
    for document in documents:
        labels = document.get("metadata", {}).get("labels", {})
        common = document.get("spec", {}).get("commonMetadata", {}).get("labels", {})
        if "malg-release" in labels or "malg-release" in common:
            raise ValueError("malg-release labels are not permitted")

    api = _workload(documents, "Deployment", "malg-api")
    api_containers = _containers(api)
    backend_tag, backend = _validate_image(
        api_containers[0].get("image"), _BACKEND_REPO, "malg-api"
    )
    if len(api_containers) != 1:
        raise ValueError("malg-api must have exactly one container")
    for kind, name in (
        ("Deployment", "malg-worker"),
        ("Job", "malg-crm-schema"),
        ("Job", "malg-migration"),
    ):
        document = _workload(documents, kind, name)
        for container in _containers(document):
            tag, image = _validate_image(container.get("image"), _BACKEND_REPO, name)
            if image != backend or tag != backend_tag:
                raise ValueError(f"{name} image does not match malg-api")

    frontend = _workload(documents, "Deployment", "frontend")
    frontend_containers = _containers(frontend)
    if len(frontend_containers) != 1:
        raise ValueError("frontend must have exactly one container")
    frontend_tag, _ = _validate_image(
        frontend_containers[0].get("image"), _FRONTEND_REPO, "frontend"
    )
    if frontend_tag != backend_tag:
        raise ValueError("backend and frontend images must use the same vX.Y.Z tag")

    migration = _workload(documents, "Job", "malg-migration")
    migration_pod = _template(migration).get("spec", {})
    if (
        migration.get("metadata", {})
        .get("annotations", {})
        .get("kustomize.toolkit.fluxcd.io/force")
        != "enabled"
    ):
        raise ValueError("migration Job must enable Flux force reconciliation")
    if migration_pod.get("automountServiceAccountToken") is not False:
        raise ValueError("migration Job must disable the service-account token")
    pod_security = migration_pod.get("securityContext", {})
    if pod_security.get("runAsNonRoot") is not True:
        raise ValueError("migration Job must run as non-root")
    for container in _containers(migration):
        security = container.get("securityContext", {})
        if (
            security.get("allowPrivilegeEscalation") is not False
            or security.get("readOnlyRootFilesystem") is not True
            or "ALL" not in security.get("capabilities", {}).get("drop", [])
        ):
            raise ValueError("migration Job containers require restrictive security settings")
        for env in container.get("env", []):
            if env.get("name") in {
                "MALG_CRM_CUTOVER_APPROVED",
                "MALG_LEGACY_CRM_RETIREMENT_APPROVED",
            }:
                raise ValueError("migration Job must not contain cutover approval env vars")
    command = migration_pod.get("containers", [{}])[0].get("command")
    if command != ["alembic", "upgrade", "head"]:
        raise ValueError("migration Job must run alembic upgrade head")
    database_ready = next(
        (c for c in migration_pod.get("initContainers", []) if c.get("name") == "database-ready"),
        None,
    )
    database_ready_command = (
        database_ready.get("command") if isinstance(database_ready, dict) else None
    )
    if (
        not isinstance(database_ready_command, list)
        or database_ready_command[:2] != ["python", "-c"]
        or len(database_ready_command) < 3
        or not isinstance(database_ready_command[2], str)
    ):
        raise ValueError("migration database-ready command must be valid Python")
    try:
        compile(database_ready_command[2], "<malg-migration database-ready>", "exec")
    except SyntaxError as exc:
        raise ValueError("migration database-ready command must be valid Python") from exc

    startup_commands = {
        "malg-api": (
            "/bin/sh -c python -m malg.database.wait_for_schema --timeout-seconds 300 "
            "&& exec uvicorn malg.api.app:app --host 0.0.0.0 --port 8000"
        ),
        "malg-worker": (
            "/bin/sh -c python -m malg.database.wait_for_schema --timeout-seconds 300 "
            "&& exec python -m malg.worker"
        ),
    }
    for name, expected in startup_commands.items():
        document = _workload(documents, "Deployment", name)
        command = _template(document).get("spec", {}).get("containers", [{}])[0].get("command")
        if command != expected.split(" ", 2):
            raise ValueError(f"{name} must wait for the bundled database schema before starting")

    crm = _workload(documents, "Kustomization", "malg-crm-schema")
    if crm.get("spec", {}).get("force") is not True:
        raise ValueError("CRM schema Kustomization must force changed Job images")
    migration_policy = _workload(documents, "CiliumNetworkPolicy", "malg-migration")
    policy_spec = migration_policy.get("spec", {})
    if (
        policy_spec.get("endpointSelector", {}).get("matchLabels", {}).get("app.kubernetes.io/name")
        != "malg-migration"
    ):
        raise ValueError("migration network policy selects the wrong workload")
    egress = policy_spec.get("egress", [])
    if not egress or policy_spec.get("ingress"):
        raise ValueError("migration network policy must be egress-only")
    allowed_ports: set[tuple[str, str]] = set()
    for rule in egress:
        for port_rule in rule.get("toPorts", []):
            for port in port_rule.get("ports", []):
                allowed_ports.add((str(port.get("port")), str(port.get("protocol"))))
        if rule.get("toEntities"):
            raise ValueError("migration network policy grants an unsafe entity")
        for target in rule.get("toEndpoints", []):
            labels = target.get("matchLabels", {})
            if (
                labels.get("k8s:io.kubernetes.pod.namespace"),
                labels.get("k8s:app.kubernetes.io/name"),
            ) not in {("malg", "postgresql"), ("kube-system", None)}:
                raise ValueError("migration network policy grants an unsafe endpoint")
    if ("5432", "TCP") not in allowed_ports or not {
        ("53", "UDP"),
        ("53", "TCP"),
    }.issubset(allowed_ports):
        raise ValueError("migration network policy must allow PostgreSQL and DNS only")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    try:
        check(args.root.resolve())
    except ValueError as exc:
        print(f"MALG deployment check failed: {exc}")
        return 1
    print("MALG deployment is consistent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
