import unittest
import subprocess
import os
import tempfile
import shutil

class TestConnectInputTermination(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Set up a temporary directory for the fake JACK graph
        cls.temp_dir = tempfile.mkdtemp()
        os.environ['FAKE_JACK_DIR'] = cls.temp_dir

        # Initialize the fake JACK graph
        subprocess.run(['tests/fakes/bin/jack_lsp', '-c'], check=True)

        # Set up the environment variables for connect-input.sh
        os.environ['GX_CONNECT_SRC'] = 'system:capture_1'
        os.environ['GX_CONNECT_DST'] = 'gx_head_amp:in_0'
        os.environ['GX_CONNECT_ATTEMPTS'] = '5'  # Reduced for testing
        os.environ['GX_CONNECT_DELAY'] = '0.1'    # Reduced for testing

    @classmethod
    def tearDownClass(cls):
        # Clean up the temporary directory
        shutil.rmtree(cls.temp_dir)

    def test_termination_when_port_never_appears(self):
        # Test that the script terminates when the destination port never appears
        result = subprocess.run(['tools/connect-input.sh'],
                               capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("gave up after 5 attempts", result.stderr)

    def test_termination_when_port_already_connected(self):
        # Connect the ports manually
        subprocess.run(['tests/fakes/bin/jack_connect', 'system:capture_1', 'gx_head_amp:in_0'], check=True)

        # Test that the script terminates when the ports are already connected
        result = subprocess.run(['tools/connect-input.sh'],
                               capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn("already connected", result.stdout)

if __name__ == '__main__':
    unittest.main()
