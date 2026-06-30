import unittest

from boss_timer_gui import BossTimerApp


class FakeVar:
    def __init__(self):
        self.value = ""

    def set(self, value):
        self.value = value


class GithubScheduleSafetyTests(unittest.TestCase):
    def test_startup_without_local_schedule_requests_remote_sync(self):
        app = BossTimerApp.__new__(BossTimerApp)
        entry = {"id": "오9", "name": "오9", "schedule": "data/schedules/오9.json"}
        app.schedule_startup_remote_sync_entry = dict(entry)
        app.schedule_github_server_var = FakeVar()
        app.schedule_status_var = FakeVar()
        app._current_schedule_has_data = lambda: False
        upserted = []
        synced = []
        app._upsert_github_server_entry_locally = lambda value: upserted.append(dict(value))
        app._format_github_server_combo_text = lambda value: value
        app._sync_selected_github_schedule = lambda: synced.append(True)

        app._sync_startup_remote_schedule_if_needed()

        self.assertIsNone(app.schedule_startup_remote_sync_entry)
        self.assertEqual(upserted, [entry])
        self.assertEqual(app.schedule_github_server_var.value, "오9")
        self.assertEqual(synced, [True])

    def test_readding_server_preserves_existing_remote_schedule_file(self):
        app = BossTimerApp.__new__(BossTimerApp)
        app.schedule_github_server_entries = []
        app.schedule_github_deleted_server_ids = set()
        app._get_current_github_upload_server_entry = lambda: {"id": "내서버"}
        app._get_next_github_data_version = lambda _value=None: "2026.07.01.003"
        app._cache_github_server_entries_from_index = lambda _entries: None

        existing_wrapper = {
            "dataVersion": "2026.07.01.002",
            "contentHash": "existing-hash",
            "kind": "schedule",
            "payload": {
                "share_prefix": "오9",
                "schedule_events": [{"boss_name": "그로아"}],
                "schedule_active_entries": [],
                "schedule_control_events": [],
            },
        }
        get_calls = []

        def get_json(path):
            get_calls.append(path)
            if path == "data/schedules/오9.json":
                return existing_wrapper, "existing-sha", ""
            if get_calls.count("data/server_index.json") == 1:
                return {"servers": []}, "index-sha", ""
            return {
                "servers": [
                    {
                        "id": "오9",
                        "name": "오9",
                        "schedule": "data/schedules/오9.json",
                    }
                ]
            }, "verify-sha", ""

        app._github_get_json_file = get_json
        put_calls = []
        app._github_put_json_file = lambda path, payload, **kwargs: (put_calls.append((path, payload)), (True, ""))[1]
        cached_payloads = []
        app._save_github_local_payload_cache = lambda entry, **kwargs: cached_payloads.append((dict(entry), kwargs)) or True

        success, _message, entries = app._apply_github_server_management_changes(
            {
                "오9": {
                    "id": "오9",
                    "name": "오9",
                    "schedule": "data/schedules/오9.json",
                    "localOnly": True,
                }
            }
        )

        self.assertTrue(success)
        self.assertEqual([path for path, _payload in put_calls], ["data/server_index.json"])
        self.assertEqual(entries[0]["scheduleVersion"], "2026.07.01.002")
        self.assertEqual(entries[0]["scheduleHash"], "existing-hash")
        self.assertNotIn("localOnly", entries[0])
        self.assertEqual(len(cached_payloads), 1)


if __name__ == "__main__":
    unittest.main()
