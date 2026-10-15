import importlib.resources
import json
import shutil
import subprocess
import unittest


class StaticRouteClient:
    """Serve the package's static assets through a stub client, without a socket."""

    def get(self, path):
        if path != "/static/designer.js":
            return 404, ""
        source = (
            importlib.resources.files("forge_core")
            .joinpath("static", "designer.js")
            .read_text(encoding="utf-8")
        )
        return 200, source


class DesignerAuthenticationTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is required to run designer.js")
    def test_preset_api_auth_failures_prompt_for_sign_in(self):
        status, source = StaticRouteClient().get("/static/designer.js")
        self.assertEqual(status, 200)
        startup = "  action(init);\n"
        self.assertEqual(source.count(startup), 1)
        source = source.replace(
            startup, "  globalThis.designerTest = {request, action};\n"
        )

        runner = r"""
const {source, responseStatus} = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const nodes = {};
function element() {
  return {
    children: [],
    hidden: false,
    textContent: '',
    addEventListener() {},
    replaceChildren(...items) { this.children = items; },
    append(...items) { this.children.push(...items); }
  };
}
globalThis.document = {
  cookie: '',
  getElementById(id) { return nodes[id] ||= element(); },
  querySelector() { return null; },
  createElement() { return element(); },
  addEventListener() {}
};
globalThis.window = {addEventListener() {}};
globalThis.location = {href: 'http://example.test/designer/'};
globalThis.fetch = async () => ({
  status: responseStatus,
  ok: false,
  async json() { return {errors: ['Server unavailable.']}; }
});
require('vm').runInThisContext(source);
(async () => {
  await designerTest.action(() => designerTest.request('presets'));
  const messages = nodes.errors.children.flatMap(list =>
    list.children.map(item => item.textContent));
  process.stdout.write(JSON.stringify({
    status: nodes.status.textContent,
    errors: messages
  }));
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
        for response_status in (401, 403, 500):
            with self.subTest(response_status=response_status):
                result = subprocess.run(
                    [shutil.which("node"), "-e", runner],
                    input=json.dumps(
                        {"source": source, "responseStatus": response_status}
                    ),
                    text=True,
                    capture_output=True,
                    check=True,
                )
                output = json.loads(result.stdout)
                if response_status in (401, 403):
                    self.assertIn("sign in again", output["status"])
                    self.assertTrue(
                        any("sign in again" in message for message in output["errors"])
                    )
                else:
                    self.assertEqual(output["status"], "Request failed.")
                    self.assertEqual(output["errors"], ["Server unavailable."])


if __name__ == "__main__":
    unittest.main()
