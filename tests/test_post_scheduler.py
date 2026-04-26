from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

from operators.social.pipeline.post_scheduler import PostScheduler


def _past(minutes: int = 5) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


def _future(minutes: int = 60) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


class PostSchedulerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        db_path = Path(self.tmp.name) / 'posts.db'
        self.published: list = []

        def mock_publish(platform: str, content: str):
            self.published.append((platform, content))
            return {'success': True, 'post_id': 'mock_id'}

        self.scheduler = PostScheduler(publish_fn=mock_publish, db_path=db_path)

    def tearDown(self) -> None:
        self.scheduler.stop()
        self.tmp.cleanup()

    def test_schedule_returns_int_id(self) -> None:
        post_id = self.scheduler.schedule('linkedin', 'Hello world', _future())
        self.assertIsInstance(post_id, int)
        self.assertGreater(post_id, 0)

    def test_get_queue_shows_queued(self) -> None:
        self.scheduler.schedule('linkedin', 'Post 1', _future())
        self.scheduler.schedule('x', 'Post 2', _future())
        queue = self.scheduler.get_queue()
        self.assertEqual(len(queue), 2)

    def test_get_due_only_past(self) -> None:
        self.scheduler.schedule('linkedin', 'Past post', _past(10))
        self.scheduler.schedule('linkedin', 'Future post', _future(60))
        due = self.scheduler.get_due()
        self.assertEqual(len(due), 1)
        self.assertEqual(due[0]['content'], 'Past post')

    def test_dispatch_publishes_due_posts(self) -> None:
        self.scheduler.schedule('linkedin', 'Ready to go', _past(5))
        self.scheduler._dispatch_due()
        self.assertEqual(len(self.published), 1)
        self.assertEqual(self.published[0][0], 'linkedin')

    def test_dispatch_marks_published(self) -> None:
        self.scheduler.schedule('linkedin', 'Ready', _past(5))
        self.scheduler._dispatch_due()
        queue = self.scheduler.get_queue()
        self.assertEqual(len(queue), 0)  # published posts not in queue

    def test_failed_post_marked_failed(self) -> None:
        def fail_fn(p, c):
            return {'success': False, 'error': 'API error'}

        db_path = Path(self.tmp.name) / 'fail.db'
        sched = PostScheduler(publish_fn=fail_fn, db_path=db_path)
        sched.schedule('linkedin', 'Will fail', _past(5))
        sched._dispatch_due()
        # Failed posts remain in get_queue
        queue = sched.get_queue()
        self.assertEqual(len(queue), 1)
        self.assertEqual(queue[0]['status'], 'failed')

    def test_cancel_queued_post(self) -> None:
        post_id = self.scheduler.schedule('linkedin', 'Cancel me', _future())
        result = self.scheduler.cancel(post_id)
        self.assertTrue(result)
        queue = self.scheduler.get_queue()
        self.assertEqual(len(queue), 0)

    def test_cancel_nonexistent_returns_false(self) -> None:
        self.assertFalse(self.scheduler.cancel(999999))


if __name__ == '__main__':
    unittest.main()
