from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, call, patch

from rich.text import Text
from textual.widgets import Label, Static

from dbrowser.app import DbrowserApp
from dbrowser.browser import BrowserScreen
from dbrowser.modals import TransferOutcome, collect_sync_events
from dbrowser.progress import SyncEvent
from dbrowser.rclone import Entry, RcloneError
from dbrowser.state import DownloadLedger


def entry(name: str, *, is_dir: bool = False) -> Entry:
    return Entry(name, name, is_dir, -1 if is_dir else 12, "", "text/plain")


class BrowserTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp = Path(self.enterContext(TemporaryDirectory(prefix="dbrowser-tests-")))
        self.ledger = DownloadLedger(state_path=self.temp / "ledger.json")
        self.enterContext(patch("dbrowser.app.DownloadLedger.load_default", return_value=self.ledger))
        self.listing = self.enterContext(patch(
            "dbrowser.browser.rclone.lsjson", new_callable=AsyncMock, return_value=[],
        ))
        self.preview = self.enterContext(patch(
            "dbrowser.browser.preview_mod.render", new_callable=AsyncMock, return_value=Text("preview"),
        ))
        self.setup = self.enterContext(patch("dbrowser.rclone.run_config"))

    async def test_initial_prefix_navigation_and_preview_preserve_bucket_path(self) -> None:
        readme = entry("readme.txt")
        listings = {
            "myaws:bucket/projects": [entry("docs", is_dir=True)],
            "myaws:bucket/projects/docs": [readme],
            "myaws:bucket": [entry("projects", is_dir=True)],
            "myaws:": [entry("bucket", is_dir=True)],
        }
        self.listing.side_effect = lambda path: listings[path]
        app = DbrowserApp("myaws", initial_path="/bucket/projects/")
        async with app.run_test() as pilot:
            await pilot.pause(0.2)
            screen = app.screen
            self.assertIsInstance(screen, BrowserScreen)
            self.listing.assert_awaited_once_with("myaws:bucket/projects")
            self.assertEqual(str(screen.query_one("#breadcrumb", Label).content), "📦 myaws:/bucket/projects")

            await pilot.press("l")
            await pilot.pause(0.2)
            self.assertEqual(screen.current_path, "bucket/projects/docs")
            self.assertEqual(self.preview.call_args.args, (readme, "myaws:bucket/projects/docs/"))
            self.assertEqual(str(screen.query_one("#preview", Static).content), "preview")

            await pilot.press("h")
            await pilot.pause()
            self.assertEqual(screen.current_path, "bucket/projects")
            self.assertNotIn(call("myaws:"), self.listing.await_args_list)
            await pilot.press("h", "h")
            await pilot.pause()
            self.assertEqual(screen.current_path, "")
            self.assertIn(call("myaws:"), self.listing.await_args_list)

    async def test_default_path_still_lists_remote_root(self) -> None:
        app = DbrowserApp("myaws")
        async with app.run_test() as pilot:
            await pilot.pause()
            self.listing.assert_awaited_once_with("myaws:")
            self.assertEqual(str(app.screen.query_one("#breadcrumb", Label).content), "📦 myaws:/")

    async def test_startup_runs_tracked_sync_check(self) -> None:
        app = DbrowserApp("myaws", initial_path="bucket/projects")
        with patch.object(app, "sync_tracked_downloads", new_callable=AsyncMock) as check:
            async with app.run_test() as pilot:
                await pilot.pause()
                check.assert_awaited_once_with()

    async def test_access_failure_displays_repair_guidance_without_setup(self) -> None:
        self.listing.side_effect = RcloneError("AccessDenied [request-id]")
        app = DbrowserApp("myaws", initial_path="bucket/private")
        async with app.run_test() as pilot:
            await pilot.pause(0.2)
            error = str(app.screen.query_one("#preview", Static).content)
            for text in ("AccessDenied [request-id]", "credentials", "permissions", "region", "rclone config", "--path"):
                self.assertIn(text, error)
            self.setup.assert_not_called()

    async def test_folder_download_records_full_s3_path_for_sync(self) -> None:
        self.listing.return_value = [entry("docs", is_dir=True)]
        destination = self.temp / "downloaded-docs"
        app = DbrowserApp("myaws", initial_path="bucket/projects")
        async with app.run_test() as pilot:
            await pilot.pause()
            with patch.object(app, "push_screen_wait", new_callable=AsyncMock, side_effect=[
                destination, TransferOutcome.SUCCESS,
            ]) as modals:
                await app.screen._do_download()
            prompt = modals.call_args_list[0].args[0]
            self.assertEqual(prompt._remote_path, "myaws:bucket/projects/docs")
            self.assertEqual(prompt._default_local, Path.home() / "dbrowser-downloads/myaws/bucket/projects/docs")
            self.assertFalse(prompt._is_root)
            transfer = modals.call_args_list[1].args[0]
            self.assertEqual(transfer._remote_path, "myaws:bucket/projects/docs")

        # Reload the persisted record: sync must target the same bucket and prefix.
        records = DownloadLedger.load(self.temp / "ledger.json").tracked_records()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].remote_path, "myaws:bucket/projects/docs")
        self.assertEqual(records[0].local_path, destination.resolve())
        async def dry_run(*args, **kwargs):
            yield SyncEvent(operation="copy", path="readme.txt", is_dry_run=True)

        with patch("dbrowser.modals.rclone.sync", side_effect=dry_run) as sync:
            events = await collect_sync_events(records[0])
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].path, "readme.txt")
        sync.assert_called_once_with(str(destination.resolve()), "myaws:bucket/projects/docs", dry_run=True)

    async def test_file_download_destination_includes_bucket_and_prefix(self) -> None:
        self.listing.return_value = [entry("readme.txt")]
        app = DbrowserApp("myaws", initial_path="bucket/projects")
        async with app.run_test() as pilot:
            await pilot.pause()
            with patch.object(app, "push_screen_wait", new_callable=AsyncMock, return_value=None) as modals:
                await app.screen._do_download()
            prompt = modals.call_args.args[0]
            self.assertEqual(prompt._remote_path, "myaws:bucket/projects/readme.txt")
            self.assertEqual(prompt._default_local, Path.home() / "dbrowser-downloads/myaws/bucket/projects")
            self.assertFalse((self.temp / "ledger.json").exists())
