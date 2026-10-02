import ast
import unittest


def extract_flags_from_list(list_node):
    tools = ["ffmpeg", "ffprobe", "mpv", "jack_lsp", "jack_connect", "jack_disconnect"]
    if not list_node.elts:
        return None, set()
    first = list_node.elts[0]
    tool_name = None
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        if first.value in tools:
            tool_name = first.value
    if not tool_name:
        return None, set()

    flags = set()
    for elt in list_node.elts[1:]:
        candidates = []
        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
            candidates.append(elt.value)
        elif isinstance(elt, ast.JoinedStr):
            for part in elt.values:
                if isinstance(part, ast.Constant) and isinstance(part.value, str):
                    candidates.append(part.value)
                    break
        elif isinstance(elt, ast.BinOp):
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
                    flags.add(flag)
    return tool_name, flags


class TestAuditFakesExtraction(unittest.TestCase):
    def test_formatted_and_concatenated_flags(self):
        code = '''
cmd1 = ["ffmpeg", f"--volume={vol}", f"-af {filter_name}"]
cmd2 = ["mpv", "--ao=" + audio_out, "--rate=%d" % rate]
cmd3 = ["ffprobe", "--format={}".format(fmt)]
'''
        tree = ast.parse(code)
        extracted = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.List):
                tool, flags = extract_flags_from_list(node)
                if tool:
                    extracted.setdefault(tool, set()).update(flags)

        self.assertIn("--volume", extracted.get("ffmpeg", set()))
        self.assertIn("-af", extracted.get("ffmpeg", set()))
        self.assertIn("--ao", extracted.get("mpv", set()))
        self.assertIn("--rate", extracted.get("mpv", set()))
        self.assertIn("--format", extracted.get("ffprobe", set()))


if __name__ == "__main__":
    unittest.main()
