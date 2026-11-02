from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import AsyncMock, patch

from dbrowser import config, rclone


class DiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_descriptions_and_arbitrary_names_do_not_hide_supported_remotes(self) -> None:
        output = (
            b"archive-account:    s3    Research archive\n"
            b"team files:         drive Shared Google Drive\n"
            b"personal:           dropbox\n"
            b"minio:              s3    Local S3-compatible service\n"
            b"other:              ftp\n\n"
        )
        with patch.object(rclone, "_run", new_callable=AsyncMock, return_value=output) as run:
            self.assertEqual(await config.discover_remotes(), [
                ("archive-account", "s3"), ("team files", "drive"),
                ("personal", "dropbox"), ("minio", "s3"),
            ])
            run.assert_awaited_once_with("listremotes", "--long")

    async def test_blank_and_incomplete_lines_are_ignored(self) -> None:
        output = b"\nno colon\nmissing-type:\n: s3\nvalid: s3\n"
        with patch.object(rclone, "_run", new_callable=AsyncMock, return_value=output):
            self.assertEqual(await rclone.list_remotes_with_types(), [("valid", "s3")])

    async def test_config_read_errors_are_not_missing_remotes(self) -> None:
        with patch.object(rclone, "_run", new_callable=AsyncMock, side_effect=rclone.RcloneError("locked")):
            with self.assertRaisesRegex(rclone.RcloneError, "locked"):
                await config.discover_remotes()


class SetupTests(unittest.TestCase):
    def test_s3_setup_delegates_credentials_to_rclone(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output), patch.object(rclone.subprocess, "run") as run:
            config.run_interactive_setup("s3")
        run.assert_called_once_with([rclone.RCLONE, "config"], check=False)
        for guidance in ("myaws", "AWS", "env_auth = true", "env_auth = false", "session_token", "Region"):
            self.assertIn(guidance, output.getvalue())

    def test_first_run_menu_includes_s3(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output), patch("builtins.input", return_value="3"):
            self.assertEqual(config.prompt_backend_choice(), "s3")
        self.assertIn("Amazon S3", output.getvalue())
