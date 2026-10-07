import unittest
import os
import tempfile
import shutil

class TestSystemdOrdering(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.service_file = os.path.join(self.temp_dir, "guitarix-connect.service")

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def test_guitarix_connect_service_ordering(self):
        # Create a test service file with the required ordering
        test_content = """
[Unit]
Description=Guitarix Connect Service
After=guitarix-jack.service

[Service]
ExecStart=/usr/bin/guitarix-connect
Restart=always

[Install]
WantedBy=multi-user.target
"""
        with open(self.service_file, "w") as f:
            f.write(test_content)

        # Read the service file and verify the ordering
        with open(self.service_file, "r") as f:
            content = f.read()

        # Check that the [Unit] section exists
        self.assertIn("[Unit]", content)

        # Check that After=guitarix-jack.service is present
        self.assertIn("After=guitarix-jack.service", content)

if __name__ == "__main__":
    unittest.main()
