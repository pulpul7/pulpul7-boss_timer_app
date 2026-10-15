"""Offline push scheduling, isolation and subscription checks. Never send a push."""
from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
from web_access_settings import AccessSettings
from web_push import (PushRuntime, _b64, push_groups, push_asset, read_push_state, write_push_state,
                      validate_subscription, validate_push_state, vapid_keys, validate_notice_times,
                      notice_candidates, validate_preferences)
from web_https_app import HTTPSApplication
from web_service_common import write_json


def subscription():
    public = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return dict(endpoint="https://fcm.googleapis.com/fcm/send/unit-test", keys=dict(p256dh=_b64(public), auth=_b64(b"a" * 16)))


def event(name, when, **kw):
    return dict(kind="boss", name=name, scheduled_at=when.isoformat(), end_at=when.isoformat(),
                catalog_id=("a" if name == "A" else "b" if name == "B" else "c") * 24, **kw)


class PushGroupTests(unittest.TestCase):
    def setUp(self):
        self.when = datetime(2026, 10, 14, 12, tzinfo=timezone(timedelta(hours=9)))
        self.prefs = dict(defaultOn=True, overrides={})

    def test_adjacent_two_minute_gaps_form_a_chain_even_when_endpoints_are_far_apart(self):
        rows = [event("D", self.when + timedelta(seconds=364)), event("C", self.when + timedelta(seconds=242)),
                event("B", self.when + timedelta(seconds=121)), event("A", self.when)]
        groups = push_groups(rows, self.prefs)
        self.assertEqual([[r["name"] for r in group] for _, group in groups], [["A", "B", "C"], ["D"]])

    def test_fractional_time_after_boundary_is_separate(self):
        groups = push_groups([event("A", self.when), event("B", self.when + timedelta(seconds=121, microseconds=1))], self.prefs)
        self.assertEqual(len(groups), 2)

    def test_disabled_and_non_boss_events_are_excluded(self):
        rows = [event("A", self.when), event("B", self.when)]
        rows += [dict(rows[0], kind="break"), dict(rows[0], kind="maintenance")]
        self.prefs["overrides"]["boss:A"] = False
        self.assertEqual([r["name"] for r in push_groups(rows, self.prefs)[0][1]], ["B"])

    def test_date_specific_override_beats_global_setting(self):
        self.prefs.update(defaultOn=False, overrides={"day:2026-10-14:boss:A": True})
        groups = push_groups([event("A", self.when), event("A", self.when + timedelta(days=1))], self.prefs)
        self.assertEqual(len(groups), 1)

    def test_disabled_boss_does_not_bridge_enabled_groups(self):
        self.prefs['overrides']['boss:B'] = False
        groups = push_groups([event('A', self.when), event('B', self.when + timedelta(seconds=121)),
                              event('C', self.when + timedelta(seconds=242))], self.prefs)
        self.assertEqual(len(groups), 2)

    def test_notice_settings_require_one_to_three_distinct_times(self):
        self.assertEqual(validate_preferences(self.prefs)['noticeTimes'], [2, None, None])
        self.assertEqual(validate_notice_times([10, 1, 0]), [10, 1, 0])
        for value in ([None, None, None], [5, 5, None], [True, None, None], [11, None, None], [5]):
            with self.assertRaises(ValueError): validate_notice_times(value)

    def test_candidate_window_uses_full_chain_and_first_boss_time(self):
        rows = [event('A', self.when), event('B', self.when + timedelta(seconds=121)),
                event('C', self.when + timedelta(seconds=242))]
        self.prefs['noticeTimes'] = [10, 1, 0]
        at = self.when.timestamp()
        for lead in (600, 60, 0):
            candidates = list(notice_candidates(rows, self.prefs, at-lead))
            self.assertEqual(len(candidates), 1)
            self.assertEqual(candidates[0][2], lead)
            self.assertEqual([r['name'] for r in candidates[0][1]], ['A', 'B', 'C'])
            self.assertFalse(list(notice_candidates(rows, self.prefs, at-lead-0.001)))
            self.assertFalse(list(notice_candidates(rows, self.prefs, at-lead+30)))


class PushRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="boss-push-unit-")
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.visitor = "a" * 32
        self.policy = AccessSettings(self.root).data
        self.policy["verifier"] = "f" * 64
        self.access = NS(lock=threading.RLock(), settings=self.policy, available=True,
            visitors={self.visitor: dict(epoch=self.policy["epoch"], role="member", ip="192.0.2.1")})
        self.scope = ["server", "guild", "18"]
        self.now = datetime.now(timezone.utc).replace(microsecond=0)
        when = self.now + timedelta(minutes=5)
        self.schedule = NS(lock=threading.Lock(), _stale=Mock(return_value=False), data=dict(
            server_name=self.scope[0], guild_name=self.scope[1], season=self.scope[2],
            events=[event("A", when), event("B", when + timedelta(seconds=10)), event("C", when + timedelta(seconds=30))]))
        self.sender = Mock()
        self.runtime = PushRuntime(self.root, "r" * 32, self.schedule, self.access, Mock(), sender=self.sender)
        self.context = dict(authenticated=True, visitor=self.visitor)
        self.payload = dict(enabled=True, subscription=subscription(), scope=self.scope,
                            prefs=dict(defaultOn=True, overrides={}, noticeTimes=[5, None, None]))
        write_json(self.root / "ddns_settings.json", dict(domain="test.duckdns.org"))
        self.gate = patch("web_authority_gate.external_allowed", return_value=True)
        self.gate.start();self.addCleanup(self.gate.stop)

    def test_five_minutes_only_group_once_and_preserve_after_restart(self):
        self.runtime.update(self.context, self.payload)
        self.runtime.tick(self.now.timestamp())
        self.assertEqual(self.sender.call_count, 1)
        notice = json.loads(self.sender.call_args.kwargs["data"])
        self.assertEqual(notice["body"], "A + B + C")
        self.assertTrue(notice["title"].startswith("5분 전"))
        self.assertEqual(self.sender.call_args.kwargs["ttl"], 60)
        self.runtime.tick(self.now.timestamp() + 1)
        self.assertEqual(self.sender.call_count, 1)
        other = PushRuntime(self.root, "new-run", self.schedule, self.access, Mock(), sender=self.sender)
        other.tick(self.now.timestamp() + 2)
        self.assertEqual(self.sender.call_count, 1)
        self.runtime.tick(self.now.timestamp() + 30)
        self.assertEqual(self.sender.call_count, 1)
        self.runtime.tick(self.now.timestamp() + 300)
        self.assertEqual(self.sender.call_count, 1, "no spawn-time notification without a spawn slot")

    def test_three_custom_times_each_send_once_and_survive_restart(self):
        self.payload['prefs']['noticeTimes'] = [10, 1, 0]
        for row in self.schedule.data['events']:
            row['scheduled_at'] = (datetime.fromisoformat(row['scheduled_at']) + timedelta(minutes=5)).isoformat()
        self.runtime.update(self.context, self.payload)
        for elapsed, label in ((0, '10분 전'), (540, '1분 전'), (600, '젠')):
            self.runtime.tick(self.now.timestamp()+elapsed)
            self.assertTrue(json.loads(self.sender.call_args.kwargs['data'])['title'].startswith(label))
            self.runtime.tick(self.now.timestamp()+elapsed+1)
        self.assertEqual(self.sender.call_count, 3)
        other = PushRuntime(self.root, 'another', self.schedule, self.access, Mock(), sender=self.sender)
        other.tick(self.now.timestamp()+601)
        self.assertEqual(self.sender.call_count, 3)

    def test_unchanged_schedule_and_preferences_reuse_group_plan(self):
        self.runtime.update(self.context, self.payload)
        with patch('web_push.push_groups', wraps=push_groups) as build:
            self.runtime.tick(self.now.timestamp())
            self.runtime.tick(self.now.timestamp()+1)
            self.runtime.tick(self.now.timestamp()+2)
            self.assertEqual(build.call_count, 1)

    def test_standby_and_stale_schedule_do_not_send(self):
        self.runtime.update(self.context, self.payload)
        with patch("web_authority_gate.external_allowed", return_value=False):
            self.runtime.tick(self.now.timestamp())
        self.schedule._stale.return_value = True
        self.runtime.tick(self.now.timestamp())
        self.sender.assert_not_called()

    def test_pin_change_ban_scope_change_and_logout_stop_sending(self):
        self.runtime.update(self.context, self.payload)
        original = self.policy["epoch"]
        self.policy["epoch"] = "b" * 32
        self.runtime.tick(self.now.timestamp());self.sender.assert_not_called()
        self.policy["epoch"] = original
        self.policy["banned_visitors"][self.visitor] = "now"
        self.runtime.tick(self.now.timestamp());self.sender.assert_not_called()
        self.policy["banned_visitors"].clear()
        self.schedule.data["guild_name"] = "other"
        self.runtime.tick(self.now.timestamp());self.sender.assert_not_called()
        self.schedule.data["guild_name"] = "guild"
        self.runtime.disable(self.context)
        self.runtime.tick(self.now.timestamp());self.sender.assert_not_called()

    def test_unchanged_preferences_do_not_rewrite_state(self):
        self.runtime.update(self.context, self.payload)
        path = self.root / "push_subscriptions.json"
        before = path.read_bytes()
        self.runtime.update(self.context, self.payload)
        self.assertEqual(path.read_bytes(), before)

    def test_encrypted_state_and_shared_key_can_be_restored(self):
        self.runtime.update(self.context, self.payload)
        raw = (self.root / "push_subscriptions.json").read_text()
        self.assertNotIn("fcm.googleapis.com", raw)
        self.assertNotIn(self.payload["subscription"]["keys"]["auth"], raw)
        state = read_push_state(self.root, self.policy)
        self.assertEqual(len(state["subscribers"]), 1)
        with tempfile.TemporaryDirectory(prefix="boss-push-receiver-unit-") as folder:
            receiver = Path(folder)
            write_push_state(receiver, self.policy, state)
            self.assertEqual(read_push_state(receiver, self.policy), state)
            self.assertEqual(vapid_keys(self.policy), vapid_keys(dict(self.policy)))

    def test_provider_failure_is_isolated_and_expired_subscription_removed(self):
        self.runtime.update(self.context, self.payload)
        error = RuntimeError("provider failure")
        error.response = NS(status_code=410)
        self.sender.side_effect = error
        self.runtime.tick(self.now.timestamp())
        self.assertFalse(self.runtime.pending)
        self.assertFalse(read_push_state(self.root, self.policy)["subscribers"])
        self.assertFalse(self.runtime.sent, "a rejected push is never committed as sent")
        self.assertFalse(self.runtime.registered(self.context))

    def test_transient_failure_retries_without_marking_as_completed(self):
        self.runtime.update(self.context, self.payload)
        self.sender.side_effect = [RuntimeError("temporary outage"), None]
        with patch("web_push.time.monotonic", return_value=100):
            self.runtime.tick(self.now.timestamp())
        self.assertFalse(self.runtime.sent)
        self.assertFalse((self.root / "push_sent.json").exists())
        with patch("web_push.time.monotonic", return_value=105):
            self.runtime.tick(self.now.timestamp() + 5)
        self.assertEqual(self.sender.call_count, 1)
        with patch("web_push.time.monotonic", return_value=111):
            self.runtime.tick(self.now.timestamp() + 11)
        self.assertEqual(self.sender.call_count, 2)
        self.assertEqual(len(self.runtime.sent), 3)
        self.assertTrue((self.root / "push_sent.json").exists())

    def test_test_push_is_caller_only_and_throttled_without_touching_schedule_history(self):
        self.runtime.update(self.context, self.payload)
        result = self.runtime.test(self.context)
        self.assertTrue(result["ok"])
        self.assertEqual(self.sender.call_count, 1)
        self.assertEqual(self.sender.call_args.kwargs["subscription_info"], self.payload["subscription"])
        self.assertIn("테스트", json.loads(self.sender.call_args.kwargs["data"])["title"])
        self.assertFalse(self.runtime.sent)
        self.assertFalse(self.runtime.test(self.context)["ok"])
        self.assertEqual(self.sender.call_count, 1)
        with self.assertRaises(ValueError):
            self.runtime.test(dict(self.context, authenticated=False))

    def test_test_push_expiration_returns_repair_instruction(self):
        self.runtime.update(self.context, self.payload)
        error = RuntimeError("expired")
        error.response = NS(status_code=410)
        self.sender.side_effect = error
        result = self.runtime.test(self.context)
        self.assertFalse(result["ok"])
        self.assertTrue(result["renew"])
        self.assertFalse(self.runtime.registered(self.context))

    def test_cancelled_send_leaves_no_success_record(self):
        self.runtime.update(self.context, self.payload)
        gates = iter([True, False])
        with patch("web_authority_gate.external_allowed", side_effect=lambda *args: next(gates)):
            self.runtime.tick(self.now.timestamp())
        self.sender.assert_not_called()
        self.assertFalse(self.runtime.sent)
        self.assertFalse(self.runtime.pending)

    def test_alias_update_does_not_send_the_same_occurrence_again(self):
        self.runtime.update(self.context, self.payload)
        self.runtime.tick(self.now.timestamp())
        self.schedule.data["events"][0]["name"] = "renamed A"
        self.runtime.tick(self.now.timestamp() + 1)
        self.assertEqual(self.sender.call_count, 1)

    def test_special_category_keeps_distinct_event_occurrences(self):
        from web_schedule_snapshot import _catalog_id
        rows = self.schedule.data["events"]
        rows[0].update(name="사용자 일정 A", catalog_id=_catalog_id("boss", "사용자이벤트"))
        self.runtime.update(self.context, self.payload)
        self.runtime.tick(self.now.timestamp())
        rows.append(dict(rows[0], name="사용자 일정 B"))
        self.runtime.tick(self.now.timestamp() + 1)
        self.assertEqual(self.sender.call_count, 2)
        self.assertEqual(json.loads(self.sender.call_args.kwargs["data"])["body"], "사용자 일정 B")

    def test_arbitrary_endpoint_and_malformed_key_rejected(self):
        for endpoint in ["http://fcm.googleapis.com/fcm/send/x", "https://127.0.0.1/x",
                         "https://fcm.googleapis.com.evil.test/x", "https://evil.push.apple.com:8443/x"]:
            value = subscription();value["endpoint"] = endpoint
            with self.assertRaises(ValueError): validate_subscription(value)
        value = subscription();value["keys"]["auth"] = "x"
        with self.assertRaises(ValueError): validate_subscription(value)

    def test_real_subscription_keys_and_manifest_worker(self):
        self.assertEqual(validate_subscription(subscription())["endpoint"], self.payload["subscription"]["endpoint"])
        public, private = vapid_keys(self.policy)
        self.assertEqual(len(public), 87)
        self.assertGreater(len(private), 100)
        self.assertIn(b"showNotification", push_asset("/push-worker.js")[0])
        self.assertNotIn(b"fetch'", push_asset("/push-worker.js")[0])
        self.assertEqual(json.loads(push_asset("/manifest.webmanifest")[0])["display"], "standalone")

    def test_https_authentication_origin_and_read_only_schedule(self):
        access = NS(context=Mock(return_value=dict(self.context, cookies=[], blocked=False)), read_wait=Mock(return_value=0))
        app = HTTPSApplication("test.duckdns.org", 8766, self.schedule, access, Mock(), authority_check=lambda: True,
            assets={k:b"" for k in ("index.html", "login.html", "schedule.js", "login.js", "style.css")}, push=self.runtime)
        def call(payload, origin="https://test.duckdns.org:8766", auth=True):
            access.context.return_value["authenticated"] = auth
            raw = json.dumps(payload).encode();answer = {}
            environ = dict(REQUEST_METHOD="POST", PATH_INFO="/api/push", HTTP_HOST="test.duckdns.org:8766",
                REMOTE_ADDR="192.0.2.1", HTTP_ORIGIN=origin, CONTENT_TYPE="application/json", CONTENT_LENGTH=str(len(raw)))
            environ.update({"wsgi.url_scheme":"https", "wsgi.input":BytesIO(raw)})
            body = app(environ, lambda status, headers: answer.update(code=int(status.split()[0])))
            return answer["code"], json.loads(b"".join(body))
        self.assertEqual(call(self.payload, auth=False)[0], 401)
        self.assertEqual(call(self.payload, origin="https://evil.test")[0], 403)
        self.assertEqual(call(self.payload)[0], 200)
        self.assertEqual(call(dict(enabled=False))[0], 200)
        self.assertEqual(len(self.schedule.data["events"]), 3)


if __name__ == "__main__":
    unittest.main()
