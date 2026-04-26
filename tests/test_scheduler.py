from __future__ import annotations

import threading
import time
import unittest

from cascadia.automation.scheduler import Scheduler, ScheduledJob


class SchedulerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sched = Scheduler(poll_interval=1)

    def tearDown(self) -> None:
        self.sched.stop()

    def test_add_job_registers(self) -> None:
        self.sched.add_job('test', '07:00', lambda: None)
        jobs = self.sched.list_jobs()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]['name'], 'test')

    def test_remove_job(self) -> None:
        self.sched.add_job('test', '07:00', lambda: None)
        removed = self.sched.remove_job('test')
        self.assertTrue(removed)
        self.assertEqual(len(self.sched.list_jobs()), 0)

    def test_remove_missing_job_returns_false(self) -> None:
        self.assertFalse(self.sched.remove_job('nonexistent'))

    def test_job_fires_on_matching_time(self) -> None:
        from datetime import datetime, timezone
        fired = threading.Event()
        job = ScheduledJob(
            name='test_fire',
            schedule='07:00',
            trigger_fn=fired.set,
        )
        # Force should_fire to True for current time
        now = datetime.now(timezone.utc)
        schedule_hhmm = f'{now.hour:02d}:{now.minute:02d}'
        job.schedule = schedule_hhmm
        self.assertTrue(job.should_fire(now))
        job.fire()
        self.assertTrue(fired.is_set())
        # Should not fire again (last_fired set)
        self.assertFalse(job.should_fire(now))

    def test_disabled_job_does_not_fire(self) -> None:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        schedule_hhmm = f'{now.hour:02d}:{now.minute:02d}'
        job = ScheduledJob(name='disabled', schedule=schedule_hhmm, trigger_fn=lambda: None, enabled=False)
        self.assertFalse(job.should_fire(now))

    def test_day_filter_fri(self) -> None:
        from datetime import datetime, timezone
        # Find a Friday
        import calendar
        now = datetime.now(timezone.utc)
        days_until_fri = (4 - now.weekday()) % 7
        from datetime import timedelta
        friday = now + timedelta(days=days_until_fri)
        friday_at_17 = friday.replace(hour=17, minute=0)
        job = ScheduledJob(name='weekly', schedule='FRI 17:00', trigger_fn=lambda: None)
        self.assertTrue(job.should_fire(friday_at_17))

    def test_scheduler_start_stop(self) -> None:
        self.sched.start()
        self.assertTrue(self.sched._thread.is_alive())
        self.sched.stop()
        self.sched._thread.join(timeout=3)
        self.assertFalse(self.sched._thread.is_alive())

    def test_job_to_dict(self) -> None:
        job = ScheduledJob(name='j', schedule='07:00', trigger_fn=lambda: None)
        d = job.to_dict()
        self.assertIn('name', d)
        self.assertIn('schedule', d)
        self.assertIn('enabled', d)
        self.assertIn('fire_count', d)


if __name__ == '__main__':
    unittest.main()
