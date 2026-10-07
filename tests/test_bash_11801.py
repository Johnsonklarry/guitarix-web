import json
import os
import shutil
import tempfile
import unittest

from tests.fakes import fakejack


class FakeJackFaultTest(unittest.TestCase):
    """Fault injection in the fake JACK tools (issue #25851)."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self._old_env = os.environ.get('FAKE_JACK_DIR')
        os.environ['FAKE_JACK_DIR'] = self.dir
        # Each test starts from the seeded default rig and no faults.
        self._old_failures = list(fakejack.injected_failures)
        del fakejack.injected_failures[:]

    def tearDown(self):
        del fakejack.injected_failures[:]
        fakejack.injected_failures.extend(self._old_failures)
        if self._old_env is None:
            os.environ.pop('FAKE_JACK_DIR', None)
        else:
            os.environ['FAKE_JACK_DIR'] = self._old_env
        shutil.rmtree(self.dir, ignore_errors=True)

    def _write_faults(self, faults):
        with open(os.path.join(self.dir, 'faults.json'), 'w') as f:
            json.dump(faults, f)

    def test_no_faults_file_leaves_behaviour_unchanged(self):
        self.assertTrue(fakejack.connect('system:capture_1',
                                         'gx_head_fx:in_0'))
        self.assertEqual(
            fakejack.connections_of('gx_head_fx:in_0'),
            ['system:capture_1'])
        self.assertEqual(fakejack.injected_failures, [])

    def test_dropped_connect_returns_false_and_leaves_graph_untouched(self):
        self._write_faults([{'op': 'connect',
                             'src': 'system:capture_1',
                             'dst': 'gx_head_fx:in_0',
                             'result': 'drop'}])

        self.assertFalse(fakejack.connect('system:capture_1',
                                          'gx_head_fx:in_0'))
        # The graph must be exactly as it was: no edge, no peer reported.
        self.assertEqual(fakejack.connections_of('gx_head_fx:in_0'), [])
        self.assertEqual(fakejack.connections_of('system:capture_1'),
                         ['gx_head_amp:in_0'])
        self.assertEqual(fakejack.injected_failures,
                         [('connect', 'system:capture_1',
                           'gx_head_fx:in_0')])

    def test_dropped_connect_matches_either_argument_order(self):
        self._write_faults([{'op': 'connect',
                             'src': 'gx_head_fx:in_0',
                             'dst': 'system:capture_1',
                             'result': 'drop'}])

        self.assertFalse(fakejack.connect('system:capture_1',
                                          'gx_head_fx:in_0'))
        self.assertEqual(fakejack.connections_of('gx_head_fx:in_0'), [])
        self.assertEqual(fakejack.injected_failures,
                         [('connect', 'system:capture_1',
                           'gx_head_fx:in_0')])

    def test_dropped_disconnect_returns_false_and_keeps_edge(self):
        self.assertTrue(fakejack.connect('system:capture_2',
                                         'gx_head_fx:in_0'))
        self._write_faults([{'op': 'disconnect',
                             'src': 'system:capture_2',
                             'dst': 'gx_head_fx:in_0',
                             'result': 'drop'}])

        self.assertFalse(fakejack.disconnect('system:capture_2',
                                             'gx_head_fx:in_0'))
        self.assertEqual(fakejack.connections_of('gx_head_fx:in_0'),
                         ['system:capture_2'])
        self.assertEqual(fakejack.injected_failures,
                         [('disconnect', 'system:capture_2',
                           'gx_head_fx:in_0')])

    def test_delay_connect_proceeds_normally(self):
        self._write_faults([{'op': 'connect',
                             'src': 'system:capture_1',
                             'dst': 'gx_head_fx:in_0',
                             'result': 'delay'}])

        self.assertTrue(fakejack.connect('system:capture_1',
                                         'gx_head_fx:in_0'))
        self.assertEqual(fakejack.connections_of('gx_head_fx:in_0'),
                         ['system:capture_1'])
        # A delay is not a failure, so nothing is recorded as injected.
        self.assertEqual(fakejack.injected_failures, [])

    def test_delay_disconnect_proceeds_normally(self):
        self.assertTrue(fakejack.connect('system:capture_2',
                                         'gx_head_fx:in_0'))
        self._write_faults([{'op': 'disconnect',
                             'src': 'system:capture_2',
                             'dst': 'gx_head_fx:in_0',
                             'result': 'delay'}])

        self.assertTrue(fakejack.disconnect('system:capture_2',
                                            'gx_head_fx:in_0'))
        self.assertEqual(fakejack.connections_of('gx_head_fx:in_0'), [])
        self.assertEqual(fakejack.injected_failures, [])

    def test_fault_for_other_pair_does_not_fire(self):
        self._write_faults([{'op': 'connect',
                             'src': 'system:capture_1',
                             'dst': 'gx_head_fx:in_0',
                             'result': 'drop'}])

        self.assertTrue(fakejack.connect('system:capture_2',
                                         'gx_head_fx:in_0'))
        self.assertEqual(fakejack.connections_of('gx_head_fx:in_0'),
                         ['system:capture_2'])
        self.assertEqual(fakejack.injected_failures, [])

    def test_fault_for_other_op_does_not_fire(self):
        self._write_faults([{'op': 'disconnect',
                             'src': 'system:capture_1',
                             'dst': 'gx_head_fx:in_0',
                             'result': 'drop'}])

        self.assertTrue(fakejack.connect('system:capture_1',
                                         'gx_head_fx:in_0'))
        self.assertEqual(fakejack.connections_of('gx_head_fx:in_0'),
                         ['system:capture_1'])
        self.assertEqual(fakejack.injected_failures, [])

    def test_malformed_faults_file_fails_loudly(self):
        with open(os.path.join(self.dir, 'faults.json'), 'w') as f:
            f.write('{not json')
        with self.assertRaises(SystemExit):
            fakejack.connect('system:capture_1', 'gx_head_fx:in_0')


if __name__ == '__main__':
    unittest.main()
