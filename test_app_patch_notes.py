"""Version display uses host identity, independently of module release versions."""
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock
import unittest

from ai_update_center import AiUpdateCenter
from app_patch_notes import patch_history_text, V551_NOTES


class PatchNotesTests(unittest.TestCase):
    def test_original_app_version_is_kept_even_with_runtime_module(self):
        runtime = NS(updater=NS(app_version="different-module-host"))
        app = NS(root=NS(after=Mock()), notice_runtime=runtime)
        center = AiUpdateCenter(app, Path("unused"), "v5.3.1.fix2")
        self.assertEqual(center.app_version, "v5.3.1.fix2")
        self.assertIs(center.updater, runtime.updater)

    def test_confirmed_version_is_prepared_not_claimed_distributed(self):
        text = patch_history_text("v5.5.1")
        self.assertIn("프로그램 v5.5.1 · 배포 준비 중", text)
        self.assertIn("프로그램 v5.3.1.fix · 마지막 배포 버전", text)
        self.assertNotIn("버전 미확정", text)
        for note in V551_NOTES:
            self.assertIn(note, text)
        self.assertIn("현재 프로그램: v5.5.1", text)

    def test_cached_releases_show_their_own_versions_and_descriptions(self):
        text = patch_history_text("v5.3.1.fix2", [
            {"version": "v1.0.1", "date": "2026-09-20", "title": "알리미 수정", "description": "날짜 오류 수정"},
            {"version": "v1.0.0", "date": "2026-09-19", "title": "알리미 최초", "description": ""},
        ])
        for fragment in ("공지 / AI 모듈 v1.0.1", "날짜 오류 수정", "공지 / AI 모듈 v1.0.0", "등록된 패치 설명이 없습니다."):
            self.assertIn(fragment, text)


if __name__ == "__main__":
    unittest.main()
