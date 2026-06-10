from __future__ import annotations

import tempfile
import unittest
from unittest.mock import MagicMock, patch

from cascadia.automation.stitch import WorkflowDefinition, WorkflowStep
from cascadia.automation.workflow_runtime import WorkflowRuntime


def _make_polling_definition() -> WorkflowDefinition:
    return WorkflowDefinition(
        'recon_workflow',
        'Recon Workflow',
        [
            WorkflowStep(
                'run_recon',
                'recon',
                'recon.search',
                poll_config={
                    'poll_endpoint': 'GET /api/research/run/{run_id}',
                    'poll_status_field': 'status',
                    'poll_complete_value': 'complete',
                    'poll_timeout_seconds': 30,
                    'poll_interval_seconds': 1,
                },
            ),
        ],
    )


class TestWorkflowRuntimePolling(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = f'{self.tempdir.name}/cascadia.db'
        self.runtime = WorkflowRuntime(self.db_path)
        self.definition = _make_polling_definition()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_polling_step_calls_poll_for_completion(self) -> None:
        """Dispatch returns run_id → _poll_for_completion is called with correct args."""
        polled_result = {'status': 'complete', 'company': 'Acme Corp', 'score': 82}

        with (
            patch.object(self.runtime, '_resolve_operator_port', return_value=8002),
            patch.object(
                self.runtime, '_dispatch_to_operator',
                wraps=self.runtime._dispatch_to_operator,
            ) as mock_dispatch,
            patch.object(
                self.runtime, '_poll_for_completion', return_value=polled_result,
            ) as mock_poll,
        ):
            # Patch the inner HTTP call inside _dispatch_to_operator to return run_id
            dispatch_response = {'status': 'ok', 'run_id': 'test_run_123'}
            with patch('urllib.request.urlopen') as mock_urlopen:
                mock_resp = MagicMock()
                mock_resp.__enter__ = lambda s: s
                mock_resp.__exit__ = MagicMock(return_value=False)
                mock_resp.read.return_value = b'{"status": "ok", "run_id": "test_run_123"}'
                mock_urlopen.return_value = mock_resp

                result = self.runtime.execute('recon_workflow', self.definition, {
                    'company': 'Acme',
                })

        mock_poll.assert_called_once()
        call_kwargs = mock_poll.call_args.kwargs
        self.assertEqual(call_kwargs['port'], 8002)
        self.assertIn('/api/research/run/test_run_123', call_kwargs['poll_path'])
        self.assertEqual(call_kwargs['poll_status_field'], 'status')
        self.assertEqual(call_kwargs['poll_complete_value'], 'complete')
        self.assertEqual(call_kwargs['timeout_seconds'], 30)
        self.assertEqual(call_kwargs['interval_seconds'], 1)

        self.assertEqual(result.run_state, 'complete')
        self.assertIn('run_recon_result', result.state_snapshot)

    def test_polling_step_timeout_marks_run_failed(self) -> None:
        """When _poll_for_completion raises TimeoutError, step and run are failed."""
        with (
            patch.object(self.runtime, '_resolve_operator_port', return_value=8002),
            patch.object(
                self.runtime, '_poll_for_completion',
                side_effect=TimeoutError('polling timed out after 30s (/api/research/run/abc)'),
            ),
        ):
            with patch('urllib.request.urlopen') as mock_urlopen:
                mock_resp = MagicMock()
                mock_resp.__enter__ = lambda s: s
                mock_resp.__exit__ = MagicMock(return_value=False)
                mock_resp.read.return_value = b'{"status": "ok", "run_id": "abc"}'
                mock_urlopen.return_value = mock_resp

                result = self.runtime.execute('recon_workflow', self.definition, {
                    'company': 'Acme',
                })

        self.assertEqual(result.run_state, 'failed')
        self.assertIn('timed out', result.preview)

    def test_sync_step_skips_polling_when_no_run_id(self) -> None:
        """If dispatch returns no run_id, polling is not triggered even when poll_config set."""
        with (
            patch.object(self.runtime, '_resolve_operator_port', return_value=8002),
            patch.object(
                self.runtime, '_poll_for_completion',
            ) as mock_poll,
        ):
            # Response has no run_id — polling should be bypassed
            with patch('urllib.request.urlopen') as mock_urlopen:
                mock_resp = MagicMock()
                mock_resp.__enter__ = lambda s: s
                mock_resp.__exit__ = MagicMock(return_value=False)
                mock_resp.read.return_value = b'{"status": "ok", "company": "Acme", "score": 70}'
                mock_urlopen.return_value = mock_resp

                result = self.runtime.execute('recon_workflow', self.definition, {
                    'company': 'Acme',
                })

        mock_poll.assert_not_called()
        self.assertEqual(result.run_state, 'complete')

    def test_sync_step_without_poll_config_is_unaffected(self) -> None:
        """A step with no poll_config uses the existing synchronous dispatch path."""
        sync_definition = WorkflowDefinition(
            'sync_workflow',
            'Sync Workflow',
            [WorkflowStep('do_thing', 'some_operator', 'thing.do')],
        )
        with (
            patch.object(self.runtime, '_resolve_operator_port', return_value=9001),
            patch.object(self.runtime, '_poll_for_completion') as mock_poll,
        ):
            with patch('urllib.request.urlopen') as mock_urlopen:
                mock_resp = MagicMock()
                mock_resp.__enter__ = lambda s: s
                mock_resp.__exit__ = MagicMock(return_value=False)
                mock_resp.read.return_value = b'{"status": "ok", "result": {"done": true}}'
                mock_urlopen.return_value = mock_resp

                result = self.runtime.execute('sync_workflow', sync_definition, {})

        mock_poll.assert_not_called()
        self.assertEqual(result.run_state, 'complete')

    def test_poll_for_completion_returns_on_matching_status(self) -> None:
        """Unit test _poll_for_completion directly — returns when status matches."""
        completed = {'status': 'complete', 'data': 'xyz'}
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            import json
            mock_resp.read.return_value = json.dumps(completed).encode()
            mock_urlopen.return_value = mock_resp

            with patch('time.sleep'):
                result = self.runtime._poll_for_completion(
                    port=8002,
                    poll_path='/api/research/run/test123',
                    poll_status_field='status',
                    poll_complete_value='complete',
                    timeout_seconds=10,
                    interval_seconds=1,
                )

        self.assertEqual(result, completed)

    def test_poll_for_completion_raises_on_timeout(self) -> None:
        """Unit test _poll_for_completion — raises TimeoutError when deadline exceeded."""
        pending = {'status': 'running'}
        with patch('urllib.request.urlopen') as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            import json
            mock_resp.read.return_value = json.dumps(pending).encode()
            mock_urlopen.return_value = mock_resp

            with patch('time.sleep'), patch('time.monotonic', side_effect=[0, 0, 9999]):
                with self.assertRaises(TimeoutError) as ctx:
                    self.runtime._poll_for_completion(
                        port=8002,
                        poll_path='/api/research/run/test123',
                        poll_status_field='status',
                        poll_complete_value='complete',
                        timeout_seconds=5,
                        interval_seconds=1,
                    )

        self.assertIn('timed out', str(ctx.exception))

    def test_workflow_step_poll_config_attribute(self) -> None:
        """WorkflowStep stores poll_config and defaults to None without it."""
        step_with_poll = WorkflowStep(
            'my_step', 'my_op', 'my.action',
            poll_config={'poll_endpoint': 'GET /check/{run_id}'},
        )
        step_without_poll = WorkflowStep('other_step', 'other_op', 'other.action')

        self.assertIsNotNone(step_with_poll.poll_config)
        self.assertEqual(step_with_poll.poll_config['poll_endpoint'], 'GET /check/{run_id}')
        self.assertIsNone(step_without_poll.poll_config)
