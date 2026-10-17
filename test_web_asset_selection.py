"""Effective web files and downgrade protection, with offline unit fixtures."""
import base64
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from web_asset_store import bundled_assets, current_version, git_hash, load_assets, page_revision, stage_version, version_id
from web_page_updater import WebPageUpdater
from web_service_common import write_json


class AssetSelectionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="boss-web-assets-unit-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.bundled = bundled_assets()
        self.revision = page_revision(self.bundled)
        self.manager = SimpleNamespace(root=self.root, closed=threading.Event(), _log=Mock(),
                                       status=Mock(return_value={"enabled": True}), apply_page_update=Mock())

    def files(self, revision):
        files = deepcopy(self.bundled)
        meta = json.loads(files["metadata.json"])
        if revision is None:
            meta.pop("page_revision", None)
        else:
            meta["page_revision"] = revision
        files["metadata.json"] = json.dumps(meta).encode()
        files["style.css"] += b'\n/* downloaded fixture */\n'
        return files

    def install(self, files):
        version = version_id({name: git_hash(raw) for name, raw in files.items()})
        stage_version(self.root, version, files)
        write_json(self.root / "pages/current.json", dict(version=version, previous=""))
        return version

    def test_legacy_download_cannot_hide_rebuilt_page(self):
        files = self.files(None)
        old = self.install(files)
        logger = Mock()
        self.assertEqual(load_assets(self.root, logger), self.bundled)
        expected = version_id({name: git_hash(raw) for name, raw in self.bundled.items()})
        self.assertEqual(current_version(self.root), expected)
        self.assertEqual((self.root / "pages/versions" / old / "style.css").read_bytes(), files["style.css"])
        self.assertIn("web_pages_bundled_preferred", str(logger.mock_calls))

    def test_same_or_newer_remote_revision_remains_usable(self):
        for revision in (self.revision, self.revision + 1):
            with self.subTest(revision=revision):
                files = self.files(revision)
                version = self.install(files)
                self.assertEqual(load_assets(self.root), files)
                self.assertEqual(current_version(self.root), version)

    def test_corrupt_download_falls_back_to_bundled_page(self):
        version = self.install(self.files(self.revision + 1))
        (self.root / "pages/versions" / version / "schedule.js").write_bytes(b"broken")
        self.assertEqual(load_assets(self.root), self.bundled)

    def update(self, files):
        rows = [dict(name=name, path="web_assets/" + name, type="file", sha=git_hash(raw), size=len(raw))
                for name, raw in files.items()]
        blobs = {git_hash(raw): dict(sha=git_hash(raw), size=len(raw), encoding="base64",
                                    content=base64.b64encode(raw).decode()) for raw in files.values()}
        fetch = Mock(side_effect=lambda url: rows if "/contents/" in url else blobs[url.rsplit("/", 1)[1]])
        updater = WebPageUpdater(self.manager)
        with patch("web_page_updater.fetch_json", fetch):
            updater._update()
        return updater

    def test_manual_update_does_not_reinstall_old_repository_page(self):
        version = self.install(self.files(None))
        pointer = (self.root / "pages/current.json").read_bytes()
        updater = self.update(self.files(None))
        self.manager.apply_page_update.assert_not_called()
        self.assertEqual(updater.status()["state"], "current")
        self.assertEqual((self.root / "pages/current.json").read_bytes(), pointer)
        self.assertNotEqual(current_version(self.root), version)

    def test_compatible_new_remote_page_is_staged_and_applied(self):
        files = self.files(self.revision + 1)
        updater = self.update(files)
        expected = version_id({name: git_hash(raw) for name, raw in files.items()})
        self.manager.apply_page_update.assert_called_once_with(expected)
        self.assertEqual(updater.status()["state"], "applied")

    def test_repository_update_applies_if_worker_is_still_on_old_files(self):
        files = self.files(self.revision + 1)
        version = self.install(files)
        self.manager.status.return_value = dict(enabled=True, web_page_version='old-worker-files')
        updater = self.update(files)
        self.manager.apply_page_update.assert_called_once_with(version)
        self.assertEqual(updater.status()['state'], 'applied')

    def test_repository_update_skips_apply_only_when_worker_already_matches(self):
        files = self.files(self.revision + 1)
        version = self.install(files)
        self.manager.status.return_value = dict(enabled=True, web_page_version=version)
        updater = self.update(files)
        self.manager.apply_page_update.assert_not_called()
        self.assertEqual(updater.status()['state'], 'current')

    def local_files(self, files):
        folder = self.root / "input-assets"
        folder.mkdir()
        for name, raw in files.items():
            (folder / name).write_bytes(raw)
        return str(folder)

    def test_local_apply_uses_shared_apply_path_without_network_and_remembers_folder(self):
        files = self.files(self.revision)
        folder = self.local_files(files)
        updater = WebPageUpdater(self.manager)
        with patch("web_page_updater.fetch_json") as network:
            updater._update_local(folder)
        network.assert_not_called()
        expected = version_id({name: git_hash(raw) for name, raw in files.items()})
        self.manager.apply_page_update.assert_called_once_with(expected)
        self.assertEqual(WebPageUpdater(self.manager).local_folder(), folder)
        self.assertEqual(updater.status()["state"], "applied")

    def test_source_files_changed_under_debug_worker_request_apply(self):
        folder = self.local_files(self.bundled)
        self.manager.status.return_value = dict(enabled=True, web_page_version="old-worker-files")
        updater = WebPageUpdater(self.manager)
        updater._update_local(folder)
        self.manager.apply_page_update.assert_called_once_with(current_version(self.root))

    def test_missing_or_obsolete_local_files_do_not_restart_service(self):
        folder = self.local_files(self.files(None))
        updater = WebPageUpdater(self.manager)
        with self.assertRaises(ValueError):
            updater._update_local(folder)
        (Path(folder) / "schedule.js").unlink()
        with self.assertRaisesRegex(ValueError, "schedule.js"):
            updater._update_local(folder)
        self.manager.apply_page_update.assert_not_called()

    def test_already_applied_local_page_skips_service_restart(self):
        folder = self.local_files(self.bundled)
        self.manager.status.return_value = dict(enabled=True, web_page_version=current_version(self.root))
        updater = WebPageUpdater(self.manager)
        updater._update_local(folder)
        self.manager.apply_page_update.assert_not_called()
        self.assertEqual(updater.status()["state"], "current")

    def test_local_and_repository_requests_share_busy_guard(self):
        updater = WebPageUpdater(self.manager)
        with patch.object(updater, "start"):
            updater.request_local(str(self.root))
            with self.assertRaises(ValueError):
                updater.request()
        self.assertTrue(updater.status()["busy"])
