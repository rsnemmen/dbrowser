from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import AsyncMock, patch

from dbrowser import __main__ as cli
from dbrowser.rclone import RcloneError


class StartupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.installed = self.enterContext(patch.object(cli.rclone, "check_installed", return_value=True))
        self.remotes = self.enterContext(patch.object(
            cli.rclone, "list_remotes_with_types", new_callable=AsyncMock,
            return_value=[("archive", "s3")],
        ))
        self.setup = self.enterContext(patch.object(cli, "run_interactive_setup"))
        self.backend_choice = self.enterContext(patch.object(cli, "prompt_backend_choice", return_value="s3"))
        self.picker = self.enterContext(patch.object(cli, "pick_remote", return_value="archive"))
        self.app = self.enterContext(patch.object(cli, "DbrowserApp"))
        self.output = io.StringIO()
        self.enterContext(redirect_stdout(self.output))
        self.enterContext(redirect_stderr(self.output))

    def assert_startup_fails(self, args: list[str], message: str, code: int = 1) -> None:
        with self.assertRaises(SystemExit) as caught:
            cli.main(args)
        self.assertEqual(caught.exception.code, code)
        self.assertIn(message, self.output.getvalue())
        self.app.assert_not_called()

    def test_existing_s3_is_reused_without_setup(self) -> None:
        cli.main([])
        self.app.assert_called_once_with(remote="archive", initial_path="")
        self.app.return_value.run.assert_called_once_with()
        self.setup.assert_not_called()
        self.picker.assert_not_called()

    def test_explicit_remote_and_path(self) -> None:
        self.remotes.return_value = [("gdrive", "drive"), ("archive", "s3")]
        cli.main(["--remote", "archive", "--backend", "s3", "--path", "/my-bucket/projects/"])
        self.app.assert_called_once_with(remote="archive", initial_path="my-bucket/projects")
        self.setup.assert_not_called()
        self.picker.assert_not_called()

    def test_picker_includes_all_supported_backends(self) -> None:
        self.remotes.return_value = [
            ("files", "dropbox"), ("gdrive", "drive"), ("archive", "s3"), ("other", "ftp"),
        ]
        cli.main([])
        self.picker.assert_called_once_with([
            ("files", "dropbox"), ("gdrive", "drive"), ("archive", "s3"),
        ])

    def test_backend_limits_picker(self) -> None:
        self.remotes.return_value = [("gdrive", "drive"), ("archive", "s3"), ("research", "s3")]
        cli.main(["--backend", "s3"])
        self.picker.assert_called_once_with([("archive", "s3"), ("research", "s3")])
        self.setup.assert_not_called()

    def test_missing_s3_runs_setup_even_with_existing_drive(self) -> None:
        self.remotes.side_effect = [
            [("gdrive", "drive")], [("gdrive", "drive"), ("new-aws", "s3")],
        ]
        cli.main(["--backend", "s3", "--path", "my-bucket/projects"])
        self.setup.assert_called_once_with("s3")
        self.backend_choice.assert_not_called()
        self.app.assert_called_once_with(remote="new-aws", initial_path="my-bucket/projects")

    def test_first_run_offers_backend_choice(self) -> None:
        self.remotes.side_effect = [[], [("new-aws", "s3")]]
        cli.main([])
        self.backend_choice.assert_called_once_with()
        self.setup.assert_called_once_with("s3")
        self.app.assert_called_once_with(remote="new-aws", initial_path="")

    def test_cancelled_setup_exits_after_one_attempt(self) -> None:
        self.remotes.return_value = []
        self.assert_startup_fails(["--backend", "s3"], "No matching remote found after setup")
        self.setup.assert_called_once_with("s3")
        self.assertEqual(self.remotes.await_count, 2)

    def test_setup_cannot_fall_back_to_another_backend(self) -> None:
        self.remotes.return_value = [("gdrive", "drive")]
        self.assert_startup_fails(["--backend", "s3"], "No matching remote found after setup")
        self.picker.assert_not_called()

    def test_unknown_explicit_remote_does_not_run_setup(self) -> None:
        self.remotes.return_value = []
        self.assert_startup_fails(["--remote", "missing", "--backend", "s3"], "No rclone remote named 'missing'")
        self.setup.assert_not_called()

    def test_unsupported_explicit_remote(self) -> None:
        self.remotes.return_value = [("other", "ftp")]
        self.assert_startup_fails(["--remote", "other"], "unsupported backend 'ftp'")
        self.setup.assert_not_called()

    def test_explicit_remote_must_match_backend(self) -> None:
        self.remotes.return_value = [("gdrive", "drive")]
        self.assert_startup_fails(["--remote", "gdrive", "--backend", "s3"], "does not match '--backend s3'")
        self.setup.assert_not_called()

    def test_discovery_error_does_not_run_setup(self) -> None:
        self.remotes.side_effect = RcloneError("cannot decrypt config")
        self.assert_startup_fails(["--backend", "s3"], "could not discover rclone remotes: cannot decrypt config")
        self.setup.assert_not_called()
        self.assertIn("rclone config", self.output.getvalue())

    def test_discovery_error_after_setup_is_reported(self) -> None:
        self.remotes.side_effect = [[], RcloneError("cannot read config")]
        self.assert_startup_fails(["--backend", "s3"], "could not discover rclone remotes: cannot read config")
        self.setup.assert_called_once_with("s3")

    def test_unknown_backend_is_rejected_before_discovery(self) -> None:
        self.assert_startup_fails(["--backend", "unknown"], "invalid choice", code=2)
        self.remotes.assert_not_awaited()

    def test_missing_rclone_is_reported_before_discovery(self) -> None:
        self.installed.return_value = False
        self.assert_startup_fails([], "'rclone' not found on PATH")
        self.remotes.assert_not_awaited()

    def test_path_works_with_existing_non_s3_backends(self) -> None:
        for backend in ("dropbox", "drive"):
            with self.subTest(backend=backend):
                self.app.reset_mock()
                self.remotes.return_value = [("files", backend)]
                cli.main(["--backend", backend, "--path", "Photos/2024"])
                self.app.assert_called_once_with(remote="files", initial_path="Photos/2024")

    def test_documented_usage_examples_parse(self) -> None:
        self.remotes.return_value = [("gdrive", "drive"), ("myaws", "s3")]
        self.picker.return_value = "myaws"
        for args in (
            [], ["--remote", "gdrive"], ["--backend", "s3"],
            ["--backend", "s3", "--path", "my-bucket/projects"],
            ["--remote", "myaws", "--path", "my-bucket"],
        ):
            with self.subTest(args=args):
                self.app.reset_mock()
                cli.main(args)
                self.app.return_value.run.assert_called_once_with()
        self.setup.assert_not_called()
