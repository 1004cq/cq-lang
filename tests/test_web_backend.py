import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

from cq import Parser, tokenize
from cq_js import build_web, compile_to_js


ROOT = Path(__file__).resolve().parents[1]


class WebBackendTests(unittest.TestCase):
    def test_result_match_binding_executes_in_javascript(self):
        source = 'match Ok("saved") { Ok(message) => print(message) Err(error) => print(error) }'
        tree = Parser(tokenize(source, "match.cq")).parse()

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            script = output / "match.js"
            script.write_text(compile_to_js(tree, "match.cq"), encoding="utf-8")
            harness = textwrap.dedent(
                f"""
                global.document = {{ querySelector() {{ return null; }} }};
                require({str(ROOT / 'web' / 'cq_rt.js')!r});
                require({str(script)!r});
                """
            )
            result = subprocess.run(
                ["node", "-e", harness], check=True, capture_output=True, text=True
            )
            self.assertEqual("saved", result.stdout.strip())

    def test_example_builds_with_complete_static_assets(self):
        source = ROOT / "web" / "app.cq"
        tree = Parser(tokenize(source.read_text(encoding="utf-8"), str(source))).parse()

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            build_web(tree, str(output), source.name, "CQ")
            self.assertEqual(
                {"index.html", "app.js", "cq_rt.js", "style.css"},
                {path.name for path in output.iterdir()},
            )
            subprocess.run(["node", "--check", output / "app.js"], check=True)
            subprocess.run(["node", "--check", output / "cq_rt.js"], check=True)

            harness = textwrap.dedent(
                """
                const fs = require("fs");
                const vm = require("vm");
                const assert = require("assert");
                const directory = process.argv[1];
                const element = () => ({
                  textContent: "", innerHTML: "", value: "", scrollHeight: 0,
                  listeners: {},
                  addEventListener(name, handler) { this.listeners[name] = handler; },
                });
                const nodes = Object.fromEntries(
                  ["#app", "#log", "#count", "#plus", "#minus", "#reset"]
                    .map((selector) => [selector, element()])
                );
                global.document = {
                  title: "",
                  querySelector(selector) { return nodes[selector] || null; },
                };
                vm.runInThisContext(fs.readFileSync(`${directory}/cq_rt.js`, "utf8"));
                vm.runInThisContext(fs.readFileSync(`${directory}/app.js`, "utf8"));
                nodes["#plus"].listeners.click();
                nodes["#plus"].listeners.click();
                nodes["#minus"].listeners.click();
                assert.equal(nodes["#count"].textContent, "1");
                nodes["#reset"].listeners.click();
                assert.equal(nodes["#count"].textContent, "0");
                """
            )
            subprocess.run(["node", "-e", harness, output], check=True)


if __name__ == "__main__":
    unittest.main()
