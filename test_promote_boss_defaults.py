"""Scoped promotion checks on synthetic files only; never builds."""
import configparser
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from promote_boss_defaults import FILES, atomic_write, promote


class BossDefaultsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="boss-defaults-unit-")
        self.addCleanup(temporary.cleanup)
        self.root, self.data = (Path(temporary.name) / name for name in ("project", "data"))
        self.profile = self.data / "server_profiles/season_18/9"
        self.baseline = self.profile / "settings_rollback/baseline"
        self.write(self.data / "active_server_profile.json", json.dumps(dict(server_id="9", season_key="season_18")))
        self.write(self.baseline / "manifest.json", json.dumps(dict(groups={"alarm": [{"keep": True}]})))
        for name in ("schedule_boss_definitions.txt", "schedule_area_definitions.txt", "schedule_fixed_bosses.txt"):
            self.write(self.profile / "init" / name, "이름|약칭|시간|지역|1|색|색|1|0|1|future-field\r\n")
            for target in (self.root / "init" / name, self.data / "init" / name, self.baseline / "init" / name):
                self.write(target, "previous-default")
        self.write(self.data / "boss_timer_settings.ini", "[settings]\nschedule_color_data_enabled=true\nfixed_boss_color_data_enabled=true\nschedule_alarm_enabled=true\ndiscord_token=DO-NOT-EXPORT\n")
        for target in (self.root / "init/default_settings.ini", self.data / "init/default_settings.ini",
                       self.baseline / "boss_timer_settings.ini"):
            self.write(target, "[settings]\nschedule_color_data_enabled=false\nfixed_boss_color_data_enabled=false\nschedule_alarm_enabled=false\n")
        self.write(self.profile / "schedule_state.json", "live-schedule")
        self.write(self.baseline / "schedule_alarm_settings.json", "previous-alarm")
        self.write(self.data / "server_profiles/season_18/8/init/schedule_boss_definitions.txt", "other-server")

    @staticmethod
    def write(path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))

    def snapshot(self):
        return {p: p.read_bytes() for base in (self.root, self.data) for p in base.rglob("*") if p.is_file()}

    def test_preview_never_writes(self):
        before = self.snapshot()
        result = promote(self.root, self.data)
        self.assertFalse(result["applied"])
        self.assertEqual(self.snapshot(), before)

    def test_promotes_verbatim_and_backs_up_without_other_settings(self):
        result = promote(self.root, self.data, apply=True)
        backup = Path(result["backup"])
        for names in FILES.values():
            for name in names:
                source = (self.profile / "init" / name).read_bytes()
                for target in (self.root / "init" / name, self.data / "init" / name, self.baseline / "init" / name):
                    self.assertEqual(target.read_bytes(), source)
                self.assertEqual((backup / "project/init" / name).read_text(), "previous-default")
        config = configparser.ConfigParser()
        config.read(self.root / "init/default_settings.ini", encoding="utf-8")
        self.assertTrue(config["settings"].getboolean("schedule_color_data_enabled"))
        self.assertFalse(config["settings"].getboolean("schedule_alarm_enabled"))
        self.assertNotIn("discord_token", config["settings"])
        self.assertEqual((self.profile / "schedule_state.json").read_text(), "live-schedule")
        self.assertEqual((self.baseline / "schedule_alarm_settings.json").read_text(), "previous-alarm")
        self.assertEqual((self.data / "server_profiles/season_18/8/init/schedule_boss_definitions.txt").read_text(), "other-server")
        manifest = json.loads((self.baseline / "manifest.json").read_text())
        self.assertEqual(manifest["groups"]["alarm"], [{"keep": True}])
        self.assertEqual(manifest["refreshed_groups"], ["boss", "fixed"])

    def test_write_failure_restores_changed_defaults(self):
        before = self.snapshot()
        def fail(path, value):
            if path == self.data / "init/schedule_fixed_bosses.txt":
                raise PermissionError("simulated failure")
            atomic_write(path, value)
        with patch("promote_boss_defaults.atomic_write", side_effect=fail), self.assertRaises(PermissionError):
            promote(self.root, self.data, apply=True)
        for p, value in before.items():
            self.assertEqual(p.read_bytes(), value)

    def test_partial_source_cannot_replace_defaults(self):
        self.write(self.profile / "init/schedule_boss_definitions.txt", "incomplete")
        before = self.snapshot()
        with self.assertRaises(ValueError):
            promote(self.root, self.data, apply=True)
        self.assertEqual(self.snapshot(), before)

    def test_optional_spectrum_promotes_alias_colors_and_backs_up_originals(self):
        from fixed_boss_aliases import catalog_id
        from test_fixed_boss_aliases import old_id
        current = self.data / 'web_service/boss_spectrum.json'
        seed = self.root / 'web_assets/boss_spectrum.json'
        old = old_id('fixed', '니다 닻')
        self.write(current, json.dumps(dict(schema=2, numbers={old: 29, 'a'*24: 17})))
        self.write(seed, json.dumps(dict(schema=2, numbers={'b'*24: 4})))
        before = current.read_bytes()
        self.assertEqual(promote(self.root, self.data, include_spectrum=True)['updated_files'], 15)
        self.assertEqual(current.read_bytes(), before)
        result = promote(self.root, self.data, apply=True, include_spectrum=True)
        self.assertEqual(current.read_bytes(), seed.read_bytes())
        self.assertEqual(json.loads(seed.read_bytes())['numbers'],
                         {catalog_id('fixed', '니다벨 닻'): 29, 'a'*24: 17})
        self.assertEqual((Path(result['backup']) / 'appdata/web_service/boss_spectrum.json').read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
