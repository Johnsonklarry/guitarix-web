import ast
import os
import sys

POSITIONAL_ONLY = ("jack_connect", "jack_disconnect")


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
