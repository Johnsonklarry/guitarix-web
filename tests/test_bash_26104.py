import os
import unittest


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
SERVICE_NAME = "guitarix-connect.service"
REQUIRED_AFTER = "guitarix-jack.service"
SKIPPED_DIRS = (".git", ".hg", ".svn", "__pycache__", ".tox", ".venv", "node_modules")


def find_service_file(root=REPO_ROOT):
    """Locate the shipped guitarix-connect.service unit inside the repository."""
    candidates = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIPPED_DIRS]
        if SERVICE_NAME in filenames:
            candidates.append(os.path.join(dirpath, SERVICE_NAME))
    if not candidates:
        return None
    # Prefer the shallowest path (the repo-root copy) and stay deterministic.
    candidates.sort(key=lambda path: (path.count(os.sep), path))
    return candidates[0]


def parse_unit_file(text):
    """Parse a systemd unit file into {section: {directive: [raw values]}}."""
    sections = {}
    current = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1].strip()
            sections.setdefault(current, {})
            continue
        if current is None or "=" not in line:
            continue
        key, _, value = line.partition("=")
        sections[current].setdefault(key.strip(), []).append(value.strip())
    return sections


def split_list_values(values):
    """Flatten repeated directives whose values may be space/comma separated."""
    items = []
    for value in values:
        for chunk in value.replace(",", " ").split():
            items.append(chunk)
    return items


class TestSystemdOrdering(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.service_path = find_service_file()
        if cls.service_path is None:
            raise AssertionError(
                "No {0} found under the repository root {1!r}; the unit file "
                "under test is missing.".format(SERVICE_NAME, REPO_ROOT)
            )
        with open(cls.service_path, "r") as handle:
            cls.content = handle.read()
        cls.sections = parse_unit_file(cls.content)

    def test_unit_section_exists(self):
        self.assertIn(
            "Unit",
            self.sections,
            "Unit file {0} has no [Unit] section".format(self.service_path),
        )

    def test_after_is_declared_in_unit_section(self):
        unit = self.sections.get("Unit", {})
        after_values = split_list_values(unit.get("After", []))
        self.assertIn(
            REQUIRED_AFTER,
            after_values,
            "{0} does not declare 'After={1}' under [Unit]".format(
                self.service_path, REQUIRED_AFTER
            ),
        )

    def test_after_is_not_declared_outside_unit_section(self):
        for section, directives in self.sections.items():
            if section == "Unit":
                continue
            self.assertNotIn(
                REQUIRED_AFTER,
                split_list_values(directives.get("After", [])),
                "Ordering directive placed outside [Unit] in {0}".format(
                    self.service_path
                ),
            )

    def test_service_section_exists(self):
        self.assertIn(
            "Service",
            self.sections,
            "Unit file {0} has no [Service] section".format(self.service_path),
        )


if __name__ == "__main__":
    unittest.main()
