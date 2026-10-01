from __future__ import annotations

import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from check_malg_deployment import check

ROOT = Path(__file__).parents[1]


class MalgDeploymentCheckerTests(unittest.TestCase):
    def copy_tree(self) -> Path:
        temp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temp)
        for name in ("infrastructure", "clusters"):
            shutil.copytree(ROOT / name, temp / name)
        return temp

    def test_coherent_digest_pinned_images_without_release_metadata(self) -> None:
        check(self.copy_tree())

    def test_worker_image_mismatch_fails(self) -> None:
        root = self.copy_tree()
        path = root / "infrastructure/malg/worker-deployment.yaml"
        text = path.read_text(encoding="utf-8")
        path.write_text(
            re.sub(r"@sha256:[0-9a-f]{64}", "@sha256:" + "0" * 64, text, count=1),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "worker image"):
            check(root)

    def test_missing_workload_fails(self) -> None:
        root = self.copy_tree()
        (root / "infrastructure/malg/worker-deployment.yaml").unlink()
        with self.assertRaisesRegex(ValueError, "missing deployment manifest"):
            check(root)

    def test_migration_safety_gate_fails_closed(self) -> None:
        root = self.copy_tree()
        path = root / "infrastructure/malg/migration-job.yaml"
        path.write_text(
            path.read_text().replace(
                '["alembic", "upgrade", "head"]', '["alembic", "upgrade", "wrong"]'
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "alembic upgrade head"):
            check(root)


if __name__ == "__main__":
    unittest.main()
