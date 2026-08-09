from __future__ import annotations

import os
import tempfile
import unittest
from importlib.metadata import version
from pathlib import Path
from unittest.mock import patch

from welearn_studio import __version__
from welearn_studio.app import (
    settings_path_from_environment,
    should_restore_last_account_file,
)


class AppEnvironmentTests(unittest.TestCase):
    def test_package_and_runtime_versions_match(self) -> None:
        self.assertEqual(version("welearn-studio"), __version__)

    def test_explicit_settings_path_is_resolved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            configured = Path(directory) / "isolated.json"
            with patch.dict(
                os.environ,
                {"WELEARN_STUDIO_SETTINGS_PATH": str(configured)},
                clear=False,
            ):
                self.assertEqual(settings_path_from_environment(), configured.resolve())

    def test_no_restore_accepts_common_truthy_values(self) -> None:
        for value in ("1", "true", "YES", "on"):
            with self.subTest(value=value):
                with patch.dict(os.environ, {"WELEARN_STUDIO_NO_RESTORE": value}, clear=False):
                    self.assertFalse(should_restore_last_account_file())

    def test_restore_remains_enabled_by_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(should_restore_last_account_file())


if __name__ == "__main__":
    unittest.main()
