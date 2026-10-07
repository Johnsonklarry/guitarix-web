import unittest
import os
import tempfile
import shutil

class TestServiceFiles(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.web_service_path = os.path.join(self.temp_dir, "guitarix-web.service")
        self.demo_service_path = os.path.join(self.temp_dir, "guitarix-demo.service")

        # Create test service files
        with open(self.web_service_path, "w") as f:
            f.write("""
[Unit]
Description=Guitarix Web Service
After=network.target

[Service]
User=guitarix
Group=guitarix
WorkingDirectory=/var/lib/guitarix
ExecStart=/usr/bin/guitarix-web
Restart=always

[Install]
WantedBy=multi-user.target
""")

        with open(self.demo_service_path, "w") as f:
            f.write("""
[Unit]
Description=Guitarix Demo Service
After=network.target

[Service]
User=guitarix
Group=guitarix
WorkingDirectory=/var/lib/guitarix
ExecStart=/usr/bin/guitarix-demo
Restart=always

[Install]
WantedBy=multi-user.target
""")

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def parse_service_file(self, file_path):
        sections = {}
        current_section = None

        with open(file_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue

                if line.startswith("[") and line.endswith("]"):
                    current_section = line[1:-1]
                    sections[current_section] = {}
                elif "=" in line and current_section:
                    key, value = line.split("=", 1)
                    sections[current_section][key.strip()] = value.strip()

        return sections

    def test_service_files_match(self):
        web_sections = self.parse_service_file(self.web_service_path)
        demo_sections = self.parse_service_file(self.demo_service_path)

        # Check required shared directives
        required_directives = {
            "User": "guitarix",
            "Group": "guitarix",
            "WorkingDirectory": "/var/lib/guitarix",
            "Restart": "always"
        }

        for directive, expected_value in required_directives.items():
            web_value = web_sections.get("Service", {}).get(directive)
            demo_value = demo_sections.get("Service", {}).get(directive)

            self.assertEqual(web_value, expected_value,
                            f"guitarix-web.service missing or incorrect {directive}: {web_value}")
            self.assertEqual(demo_value, expected_value,
                            f"guitarix-demo.service missing or incorrect {directive}: {demo_value}")

            if web_value != demo_value:
                self.fail(f"Shared directive {directive} differs between services: "
                         f"web={web_value}, demo={demo_value}")

if __name__ == "__main__":
    unittest.main()
