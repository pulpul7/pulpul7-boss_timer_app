from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest

from audio_pipeline import (
    AudioPipeline,
    AudioSourceKind,
    OutputConditionEvaluator,
    OutputDecision,
    OutputTarget,
    PlaybackRequestFactory,
)


class AudioPipelineTests(unittest.TestCase):
    def test_file_source_is_preferred_over_tts_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            clip_path = Path(temp_dir) / "alert.wav"
            clip_path.write_bytes(b"RIFF")
            request = PlaybackRequestFactory().create_legacy_request(
                {
                    "phase": "PRE_ALERT",
                    "target_time": datetime.now(),
                    "clip_paths": [str(clip_path)],
                    "fallback_text": "file fallback sample",
                }
            )

        self.assertEqual(request.source.kind, AudioSourceKind.FILE)
        self.assertTrue(request.source.ready)

    def test_compatibility_scheduler_routes_expired_legacy_request(self) -> None:
        pipeline = AudioPipeline(compatibility_order=True)
        delivered = []
        pipeline.router.register(OutputTarget.LEGACY, delivered.append)
        pipeline.submit_legacy(
            {
                "phase": "SPAWN_CONFIRMED",
                "target_time": datetime.now(),
                "fallback_text": "expired sample",
                "expires_at": datetime.now() - timedelta(seconds=1),
            }
        )
        batch = pipeline.scheduler.collect(pipeline.queue, timeout=0.1, collect_window=0)
        self.assertIsNotNone(batch)
        for request in batch or []:
            pipeline.router.route(request)
        pipeline.queue.task_done()

        self.assertEqual(len(delivered), 1)
        self.assertFalse(OutputConditionEvaluator(enforce_deadline=True).evaluate(delivered[0]).allowed)

    def test_injected_policy_rejects_generation_change_before_routing(self) -> None:
        pipeline = AudioPipeline(
            evaluator=OutputConditionEvaluator(
                policy=lambda request, _now: OutputDecision(
                    request.generation == 7,
                    "generation_changed" if request.generation != 7 else "",
                )
            )
        )
        pipeline.submit_legacy(
            {
                "phase": "PRE_ALERT",
                "target_time": datetime.now(),
                "fallback_text": "old generation",
                "generation": 6,
            }
        )
        batch = pipeline.scheduler.collect(pipeline.queue, timeout=0.1, collect_window=0)
        pipeline.queue.task_done()

        self.assertEqual(batch, [])
        self.assertEqual(len(pipeline.scheduler.last_rejections), 1)
        self.assertEqual(pipeline.scheduler.last_rejections[0][1].reason, "generation_changed")


if __name__ == "__main__":
    unittest.main()
