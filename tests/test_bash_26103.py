import unittest
import os

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SERVICE_FILE_NAMES = ("guitarix-web.service", "guitarix-demo.service")

# Locations where the unit files are installed on a live system.
SYSTEM_SEARCH_DIRS = (
    "/etc/systemd/system",
    "/usr/lib/systemd/system",
    "/lib/systemd/system",
    "/usr/local/lib/systemd/system",
)

# Directories pruned while scanning the project tree for the shipped unit files.
PRUNED_DIRS = (".git", "__pycache__", "node_modules", ".tox", "build", "dist")


def locate_service_files():
    """Locate the shipped guitarix unit files.

    The real files are looked up in the project tree first and in the system
    install locations second. They are deliberately never synthesized here: a
    test that writes its own inputs cannot detect a regression in the fix.
    """
    found = {}

    for dirpath, dirnames, filenames in os.walk(REPO_ROOT):
        dirnames[:] = [d for d in dirnames if d not in PRUNED_DIRS]
        for name in SERVICE_FILE_NAMES:
            if name in filenames and name not in found:
                found[name] = os.path.join(dirpath, name)

    for name in SERVICE_FILE_NAMES:
        if name in found:
            continue
        for directory in SYSTEM_SEARCH_DIRS:
            candidate = os.path.join(directory, name)
            if os.path.isfile(candidate):
                found[name] = candidate
                break

    return found


class TestServiceFiles(unittest.TestCase):
    def setUp(self):
        located = locate_service_files()
        missing = [name for name in SERVICE_FILE_NAMES if name not in located]
        if missing:
            self.fail(
                "Could not locate the shipped unit file(s) {0}; searched the project "
                "tree under {1} and {2}. The fix ships these files, so this test must "
                "fail loudly instead of passing vacuously when they are absent.".format(
                    ", ".join(missing), REPO_ROOT, ", ".join(SYSTEM_SEARCH_DIRS)
                )
            )

        self.web_service_path = located["guitarix-web.service"]
        self.demo_service_path = located["guitarix-demo.service"]

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

            self.assertEqual(
                web_value, expected_value,
                "{} missing or incorrect {}: {}".format(
                    os.path.basename(self.web_service_path), directive, web_value),
            )
            self.assertEqual(
                demo_value, expected_value,
                "{} missing or incorrect {}: {}".format(
                    os.path.basename(self.demo_service_path), directive, demo_value),
            )

    def test_shared_service_directives_are_identical(self):
        web_service = self.parse_service_file(self.web_service_path).get("Service", {})
        demo_service = self.parse_service_file(self.demo_service_path).get("Service", {})

        for directive in ("User", "Group", "WorkingDirectory", "Restart"):
            self.assertEqual(
                web_service.get(directive), demo_service.get(directive),
                "Shared directive {} differs between {} and {}: web={!r}, demo={!r}".format(
                    directive,
                    os.path.basename(self.web_service_path),
                    os.path.basename(self.demo_service_path),
                    web_service.get(directive),
                    demo_service.get(directive),
                ),
            )

if __name__ == "__main__":
    unittest.main()
