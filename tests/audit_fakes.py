import ast
import os
import sys
import unittest

POSITIONAL_ONLY = ("jack_connect", "jack_disconnect")


def parse_systemd_unit(path):
    """Parse a systemd unit file into {section: {key: value}}.

    Directives are stored under the section they appear in. Lines that are
    blank, comments, or section headers are handled; a directive repeated in
    the same section keeps the last value seen. This works for any unit file,
    including guitarix-web.service, guitarix-demo.service and
    guitarix-connect.service.
    """
    sections = {}
    current = None
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or line.startswith(";"):
                continue
            if line.startswith("[") and line.endswith("]"):
                current = line[1:-1]
                sections.setdefault(current, {})
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if current is None:
                current = ""
                sections.setdefault(current, {})
            sections[current][key] = value
    return sections


def _unit_path(name):
    tests_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(tests_dir)
    return os.path.join(project_root, name)


class ServiceFileTests(unittest.TestCase):
    def test_service_files_match(self):
        web_path = _unit_path("guitarix-web.service")
        demo_path = _unit_path("guitarix-demo.service")

        web = parse_systemd_unit(web_path)
        demo = parse_systemd_unit(demo_path)

        web_service = web.get("Service", {})
        demo_service = demo.get("Service", {})

        for key in ("ExecStart", "WorkingDirectory", "Restart"):
            self.assertIn(
                key,
                web_service,
                "guitarix-web.service [Service] is missing required key %r; got %r"
                % (key, sorted(web_service)),
            )

        for key in ("User", "Group", "WorkingDirectory", "Restart"):
            self.assertIn(
                key,
                demo_service,
                "guitarix-demo.service [Service] is missing required key %r; got %r"
                % (key, sorted(demo_service)),
            )
            self.assertEqual(
                web_service.get(key),
                demo_service.get(key),
                "guitarix-web.service and guitarix-demo.service disagree on %r: "
                "web=%r demo=%r"
                % (key, web_service.get(key), demo_service.get(key)),
            )

    def test_demo_service_deployed_spec(self):
        demo_path = _unit_path("guitarix-demo.service")
        demo = parse_systemd_unit(demo_path)

        self.assertEqual(
            sorted(demo),
            ["Install", "Service", "Unit"],
            "guitarix-demo.service must define exactly the [Unit], [Service] "
            "and [Install] sections; got %r" % (sorted(demo),),
        )

        unit = demo["Unit"]
        service = demo["Service"]
        install = demo["Install"]

        self.assertEqual(
            unit.get("Description"),
            "Guitarix demo web interface",
            "guitarix-demo.service [Unit] Description mismatch: %r"
            % (unit.get("Description"),),
        )
        self.assertEqual(
            unit.get("After"),
            "network.target sound.target guitarix-jack.service",
            "guitarix-demo.service [Unit] After mismatch: %r"
            % (unit.get("After"),),
        )
        self.assertEqual(
            unit.get("Wants"),
            "guitarix-jack.service",
            "guitarix-demo.service [Unit] Wants mismatch: %r"
            % (unit.get("Wants"),),
        )

        self.assertEqual(
            service.get("Type"),
            "simple",
            "guitarix-demo.service [Service] Type mismatch: %r"
            % (service.get("Type"),),
        )
        self.assertEqual(
            service.get("User"),
            "guitarix",
            "guitarix-demo.service [Service] User mismatch: %r"
            % (service.get("User"),),
        )
        self.assertEqual(
            service.get("Group"),
            "guitarix",
            "guitarix-demo.service [Service] Group mismatch: %r"
            % (service.get("Group"),),
        )
        self.assertEqual(
            service.get("WorkingDirectory"),
            "/opt/guitarix",
            "guitarix-demo.service [Service] WorkingDirectory mismatch: %r"
            % (service.get("WorkingDirectory"),),
        )
        self.assertEqual(
            service.get("ExecStart"),
            "/opt/guitarix/venv/bin/python -m guitarix.web --demo",
            "guitarix-demo.service [Service] ExecStart mismatch: %r"
            % (service.get("ExecStart"),),
        )
        self.assertEqual(
            service.get("Restart"),
            "on-failure",
            "guitarix-demo.service [Service] Restart mismatch: %r"
            % (service.get("Restart"),),
        )
        self.assertEqual(
            service.get("Environment"),
            "GUITARIX_DEMO=1",
            "guitarix-demo.service [Service] Environment mismatch: %r"
            % (service.get("Environment"),),
        )

        self.assertEqual(
            install.get("WantedBy"),
            "multi-user.target",
            "guitarix-demo.service [Install] WantedBy mismatch: %r"
            % (install.get("WantedBy"),),
        )


def main():
    tests_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(tests_dir)
    fakes_bin = os.path.join(tests_dir, "fakes", "bin")
    
    # Tools to scan for
    tools = ["ffmpeg", "ffprobe", "mpv", "jack_lsp", "jack_connect", "jack_disconnect"]
    
    # Collect project flags
    project_flags = {}
    for tool in tools:
        project_flags[tool] = set()
    
    # Scan .py files in project root, excluding tests/ and .venv/
    for root, dirs, files in os.walk(project_root):
        # Skip tests and venv directories
        dirs[:] = [d for d in dirs if d not in ("tests", ".venv")]
        
        for file in files:
            if file.endswith(".py"):
                filepath = os.path.join(root, file)
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        content = f.read()
                except (OSError, UnicodeDecodeError):
                    continue
                
                try:
                    tree = ast.parse(content)
                except SyntaxError:
                    continue
                
                # Find lists that start with tool names
                for node in ast.walk(tree):
                    if isinstance(node, ast.List):
                        # Check if list starts with a tool name
                        if not node.elts:
                            continue
                        first = node.elts[0]
                        tool_name = None
                        
                        # Direct string literal
                        if isinstance(first, ast.Constant) and isinstance(first.value, str):
                            if first.value in tools:
                                tool_name = first.value
                        # Variable reference
                        elif isinstance(first, ast.Name):
                            # Look for assignments to this variable
                            for assign_node in ast.walk(tree):
                                if (isinstance(assign_node, ast.Assign) and 
                                    len(assign_node.targets) == 1 and 
                                    isinstance(assign_node.targets[0], ast.Name) and 
                                    assign_node.targets[0].id == first.id and 
                                    isinstance(assign_node.value, ast.Constant) and 
                                    isinstance(assign_node.value.value, str) and 
                                    assign_node.value.value in tools):
                                    tool_name = assign_node.value.value
                        
                        if tool_name:
                            # Collect flags from the list
                            for elt in node.elts[1:]:  # Skip first element (tool name)
                                candidates = []
                                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                    candidates.append(elt.value)
                                elif isinstance(elt, ast.JoinedStr):
                                    for part in elt.values:
                                        if isinstance(part, ast.Constant) and isinstance(part.value, str):
                                            candidates.append(part.value)
                                            break
                                elif isinstance(elt, ast.BinOp):
                                    # string concatenation or % formatting
                                    cur = elt
                                    while isinstance(cur, ast.BinOp):
                                        cur = cur.left
                                    if isinstance(cur, ast.Constant) and isinstance(cur.value, str):
                                        candidates.append(cur.value)
                                elif isinstance(elt, ast.Call) and isinstance(elt.func, ast.Attribute) and elt.func.attr == "format":
                                    if isinstance(elt.func.value, ast.Constant) and isinstance(elt.func.value.value, str):
                                        candidates.append(elt.func.value.value)

                                for text in candidates:
                                    if text.startswith("-"):
                                        flag = text.split("=")[0].split()[0]
                                        if "%" in flag:
                                            flag = flag.split("%")[0]
                                        if "{" in flag:
                                            flag = flag.split("{")[0]
                                        if flag.startswith("-"):
                                            project_flags[tool_name].add(flag)
    
    # Read fake files and collect their flags
    fake_flags = {}
    for tool in tools:
        fake_path = os.path.join(fakes_bin, tool)
        fake_flags[tool] = set()
        if not os.path.exists(fake_path):
            continue
        try:
            with open(fake_path, "r", encoding="utf-8") as f:
                fake_content = f.read()
        except (OSError, UnicodeDecodeError):
            continue
        
        # Parse fake file for string literals starting with '-'
        try:
            fake_tree = ast.parse(fake_content)
        except SyntaxError:
            # If parsing fails, read as text
            lines = fake_content.splitlines()
            for line in lines:
                parts = line.split()
                for part in parts:
                    if part.startswith("-"):
                        if "=" in part:
                            fake_flags[tool].add(part.split("=")[0])
                        else:
                            fake_flags[tool].add(part)
        else:
            # Parse AST
            for node in ast.walk(fake_tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    flag = node.value
                    if flag.startswith("-"):
                        if "=" in flag:
                            fake_flags[tool].add(flag.split("=")[0])
                        else:
                            fake_flags[tool].add(flag)
    
    # Report differences
    all_good = True
    print("Audit of fakes vs project flags:")
    print("====================================")
    
    for tool in tools:
        proj_flags = project_flags[tool]
        fake_flags_set = fake_flags[tool]
        
        if not proj_flags:
            # jack_connect and jack_disconnect take two port names and no
            # options at all, so finding nothing is the right answer for
            # them and a broken scan for everything else.
            if tool in POSITIONAL_ONLY:
                print("")
                print(f"{tool}:")
                print("  takes no flags -- nothing to be more forgiving about")
            else:
                print("")
                print(f"{tool}: ERROR - no flags found, so the scan failed")
                all_good = False
            continue
        
        print(f"\n{tool}:")
        print(f"  Project flags: {sorted(proj_flags)}")
        print(f"  Fake flags:    {sorted(fake_flags_set)}")
        
        missing = proj_flags - fake_flags_set
        if missing:
            print(f"  Missing in fake: {sorted(missing)}")
            all_good = False
        else:
            print("  All project flags handled by fake")
    
    if not all_good:
        sys.exit(1)
    
if __name__ == "__main__":
    main()
