import copy
import unittest

import yaml

from scripts import check_flux_alerting


class FluxAlertingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.notifications = list(
            yaml.safe_load_all(check_flux_alerting.FLUX_NOTIFICATIONS.read_text())
        )
        cls.platform_alerts = yaml.safe_load(
            check_flux_alerting.PLATFORM_ALERTS.read_text()
        )
        cls.prometheus_stack = yaml.safe_load(
            check_flux_alerting.PROMETHEUS_STACK.read_text()
        )
        cls.observability_config = yaml.safe_load(
            check_flux_alerting.OBSERVABILITY_CONFIG.read_text()
        )

    def validate(
        self,
        notifications: list | None = None,
        platform_alerts: dict | None = None,
        observability_config: dict | None = None,
    ) -> None:
        check_flux_alerting.validate_documents(
            notifications or self.notifications,
            platform_alerts or self.platform_alerts,
            self.prometheus_stack,
            observability_config or self.observability_config,
        )

    def test_repository_alerting_is_stateful(self) -> None:
        self.validate()

    def test_rejects_kustomization_event_email_loop(self) -> None:
        notifications = copy.deepcopy(self.notifications)
        event_alert = next(
            document
            for document in notifications
            if document.get("metadata", {}).get("name") == "reconciliation-errors"
        )
        event_alert["spec"]["eventSources"].append(
            {"kind": "Kustomization", "name": "*"}
        )

        with self.assertRaisesRegex(ValueError, "statefully monitored"):
            self.validate(notifications=notifications)

    def test_rejects_alerting_dependency_on_litellm(self) -> None:
        observability_config = copy.deepcopy(self.observability_config)
        observability_config["spec"]["dependsOn"].append({"name": "litellm"})

        with self.assertRaisesRegex(ValueError, "must not depend on LiteLLM"):
            self.validate(observability_config=observability_config)


if __name__ == "__main__":
    unittest.main()
