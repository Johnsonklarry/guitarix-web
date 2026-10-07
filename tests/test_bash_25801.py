import json
import os
import shutil
import tempfile
import unittest

from tests.fakes import fakejack


class FakeJackFaultInjectionTest(unittest.TestCase):
    """Fault injection in the fake JACK tools must be opt-in and persisted."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self._old = os.environ.get('FAKE_JACK_DIR')
        os.environ['FAKE_JACK_DIR'] = self.dir

    def tearDown(self):
        if self._old is None:
            os.environ.pop('FAKE_JACK_DIR', None)
        else:
            os.environ['FAKE_JACK_DIR'] = self._old
        shutil.rmtree(self.dir, ignore_errors=True)

    def _graph(self):
        with open(os.path.join(self.dir, 'graph.json')) as f:
            return json.load(f)

    def test_no_faults_preserves_existing_behaviour(self):
        self.assertFalse(fakejack.connect('system:capture_1',
                                          'gx_head_amp:in_0'))  # seeded rig
        self.assertTrue(fakejack.connect('system:capture_2',
                                         'gx_head_amp:in_0'))
        self.assertTrue(fakejack.disconnect('system:capture_2',
                                            'gx_head_amp:in_0'))
        self.assertEqual(fakejack.fault_log(), [])
        self.assertEqual(fakejack.faults(), {})

    def test_dropped_connect_leaves_graph_unchanged_and_logs(self):
        fakejack.set_faults(drop_connect=1)

        self.assertFalse(fakejack.connect('system:capture_2',
                                          'gx_head_amp:in_0'))

        graph = self._graph()
        self.assertNotIn(['system:capture_2', 'gx_head_amp:in_0'],
                         graph['connections'])
        self.assertEqual(graph['fault_log'],
                         [{'fault': 'drop_connect',
                           'detail': 'system:capture_2 -> gx_head_amp:in_0'}])

    def test_dropped_connect_is_consumed_after_one_use(self):
        fakejack.set_faults(drop_connect=1)

        self.assertFalse(fakejack.connect('system:capture_2',
                                          'gx_head_amp:in_0'))
        self.assertTrue(fakejack.connect('system:capture_2',
                                         'gx_head_amp:in_0'))

        self.assertEqual(len(fakejack.fault_log()), 1)
        self.assertIn(['system:capture_2', 'gx_head_amp:in_0'],
                      self._graph()['connections'])

    def test_dropped_disconnect_leaves_graph_unchanged_and_logs(self):
        fakejack.set_faults(drop_disconnect=1)

        self.assertFalse(fakejack.disconnect('system:capture_1',
                                             'gx_head_amp:in_0'))

        graph = self._graph()
        self.assertIn(['system:capture_1', 'gx_head_amp:in_0'],
                      graph['connections'])
        self.assertEqual(graph['fault_log'],
                         [{'fault': 'drop_disconnect',
                           'detail': 'system:capture_1 -> gx_head_amp:in_0'}])

    def test_fault_settings_and_log_survive_a_fresh_load(self):
        fakejack.set_faults(drop_connect=2)
        fakejack.connect('system:capture_2', 'gx_head_amp:in_0')

        # A separate process would see the same file; simulate by re-reading.
        self.assertEqual(fakejack.faults(), {'drop_connect': 1})
        self.assertEqual(fakejack.fault_log(),
                         [{'fault': 'drop_connect',
                           'detail': 'system:capture_2 -> gx_head_amp:in_0'}])

    def test_clear_faults_restores_clean_state(self):
        fakejack.set_faults(drop_connect=1)
        fakejack.connect('system:capture_2', 'gx_head_amp:in_0')
        fakejack.clear_faults()

        self.assertEqual(fakejack.faults(), {})
        self.assertEqual(fakejack.fault_log(), [])
        self.assertTrue(fakejack.connect('system:capture_2',
                                         'gx_head_amp:in_0'))


if __name__ == '__main__':
    unittest.main()
