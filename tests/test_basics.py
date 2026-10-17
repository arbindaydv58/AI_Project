"""Basic checks for Jarvis. Run from the project folder:  python -m unittest discover tests

Nothing here needs Ollama or the internet. Jarvis data goes to a temporary folder.
"""
import atexit
import http.client
import http.server
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
_tmp = tempfile.mkdtemp(prefix="tmp-run-", dir=HERE)  # next to the tests, so nothing lands on C:


def _cleanup():
    if "app" in globals():
        app.db.conn.close()  # Windows cannot delete a folder that holds an open database
    os.chdir(HERE)
    shutil.rmtree(_tmp, ignore_errors=True)


atexit.register(_cleanup)
os.environ["JARVIS_HOME"] = str(Path(_tmp) / "home")  # must be set before jarvis is imported
os.chdir(_tmp)
sys.path.insert(0, str(HERE.parent))

from jarvis.agent import Agent  # noqa: E402
from jarvis.app import App  # noqa: E402
from jarvis.common import TagFilter, strip_think  # noqa: E402
from jarvis.engines import ChatResult, OllamaEngine  # noqa: E402
from jarvis.errors import EngineUnavailable  # noqa: E402
from jarvis.presets import PRESETS  # noqa: E402
from jarvis.privacy import redact  # noqa: E402
from jarvis.protocols import all_protocols, match_protocol, run_protocol  # noqa: E402
from jarvis.registry import TOOLS  # noqa: E402
from jarvis.scheduler import next_run  # noqa: E402
from jarvis.server import GUI_HTML, access_token, make_handler  # noqa: E402
from jarvis.sysutils import cpu_percent, lan_ip, memory_info  # noqa: E402
from jarvis.tools.basic import safe_eval  # noqa: E402

app = App()
app.memory._embed_ok = False  # keyword search only, so the tests do not depend on Ollama


def tool(name, **args):
    agent = Agent(app, preset="lite", session_id="tools", approver=lambda n, a: True)
    return agent._execute({"name": name, "arguments": args}, TOOLS, Counter())


class FakeModel:
    """Replaces app.router.chat with a scripted list of replies."""

    def __init__(self, *replies):
        self.replies, self.seen = list(replies), []

    def __call__(self, messages, tools=None, on_token=None, **kw):
        self.seen.append(list(messages))
        r = self.replies.pop(0)
        if r.content and on_token:
            on_token(r.content)
        r.latency = r.ttft = 0.1
        return r


class Text(unittest.TestCase):
    def test_calculator(self):
        self.assertEqual(safe_eval("(1234*5678)-91011"), 6915641)
        self.assertEqual(safe_eval("50%"), 0.5)
        self.assertIn("= 437", tool("calculator", expression="23*19"))
        with self.assertRaises(ValueError):
            safe_eval("__import__('os')")

    def test_reasoning_filter(self):
        leaked = "Hmm, the user wants OK.\nResponse ready.\n</think>\n\nOK"  # a thinking build closes a tag it never opened
        self.assertEqual(strip_think(leaked), "OK")
        self.assertEqual(strip_think("<think>plan</think>\n\nAnswer"), "Answer")
        self.assertEqual(strip_think("<think>unfinished"), "")
        self.assertEqual(strip_think("plain"), "plain")
        f = TagFilter()
        self.assertEqual(f.feed("a<think>x</think>b") + f.flush(), "ab")

    def test_pii_redaction(self):
        out, n = redact("mail me at a@b.com, card 4111 1111 1111 1111")
        self.assertEqual(n, 2)
        self.assertNotIn("a@b.com", out)


class Safety(unittest.TestCase):
    def test_blocked_things(self):
        if sys.platform == "win32":
            self.assertIn("outside the allowed folders", tool("read_file", path="C:/Windows/win.ini"))
        self.assertIn("sensitive", tool("read_file", path="~/.ssh/id_rsa"))
        for command in ("shutdown /s", "rm -rf /", "format c:"):
            with self.assertRaises(PermissionError):  # checked directly, so a failure here never runs the command
                app.security.command(command)
        app.security.command("dir")
        self.assertIn("private/local", tool("fetch_url", url="http://127.0.0.1:9/"))

    def test_risky_tool_asks_first(self):
        agent = Agent(app, preset="code", session_id="deny", approver=lambda n, a: False)
        out = agent._execute({"name": "write_file", "arguments": {"path": "x.txt", "content": "y"}}, TOOLS, Counter())
        self.assertIn("denied permission", out)
        self.assertFalse(Path("x.txt").exists())


class Setup(unittest.TestCase):
    def test_every_preset_tool_exists(self):
        for name, p in PRESETS.items():
            for t in p["tools"]:
                self.assertTrue(t == "*" or t in TOOLS, f"{name} preset lists unknown tool {t}")

    def test_removed_features_stay_removed(self):
        for gone in ("home_status", "home_control"):
            self.assertNotIn(gone, TOOLS)

    def test_ollama_request_options(self):
        sent = {}

        class Reply:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def __iter__(self): return iter([b'{"message": {"content": "hi"}, "done": true}\n'])

        import jarvis.engines as engines
        real = engines.http_open
        engines.http_open = lambda url, payload=None, **kw: sent.update(payload) or Reply()
        try:
            OllamaEngine("http://x", num_ctx=8192, num_thread=12, keep_alive="30m", think=False).chat(
                [{"role": "user", "content": "hi"}], "m")
        finally:
            engines.http_open = real
        self.assertEqual(sent["options"]["num_ctx"], 8192)
        self.assertEqual(sent["options"]["num_thread"], 12)
        self.assertEqual((sent["keep_alive"], sent["think"]), ("30m", False))

    def test_machine_readings(self):
        mem = memory_info()
        self.assertTrue(mem is None or 0 < mem[1] <= mem[0])
        cpu = cpu_percent()
        self.assertTrue(cpu is None or 0 <= cpu <= 100)

    def test_schedule_parsing(self):
        import datetime as dt
        monday_10 = dt.datetime(2026, 1, 5, 10, 0)
        self.assertEqual(next_run("daily 08:00", monday_10), dt.datetime(2026, 1, 6, 8, 0))
        self.assertEqual(next_run("every 30m", monday_10), dt.datetime(2026, 1, 5, 10, 30))
        with self.assertRaises(ValueError):
            next_run("whenever", monday_10)


class AgentLoop(unittest.TestCase):
    def test_tool_call_then_answer(self):
        model = FakeModel(
            ChatResult(tool_calls=[{"id": "1", "name": "calculator", "arguments": {"expression": "6*7"}}], engine="fake", model="fake"),
            ChatResult(content="It is 42.", engine="fake", model="fake"))
        real, app.router.chat = app.router.chat, model
        try:
            agent = Agent(app, preset="lite", session_id="loop")
            self.assertEqual(agent.run("what is 6*7?"), "It is 42.")
        finally:
            app.router.chat = real
        self.assertEqual(agent.last_stats["tools"], 1)
        first = model.seen[0]
        self.assertNotIn("Current date", first[0]["content"])          # the system prompt must not change from minute to minute
        self.assertTrue(first[-1]["content"].startswith("[Current date and time:"))  # the clock travels with the question
        saved = [r["content"] for r in app.db.q("SELECT content FROM messages WHERE session_id='loop' ORDER BY id")]
        self.assertEqual(saved, ["what is 6*7?", "It is 42."])          # and is not stored in the chat history

    def test_no_model_gives_a_clear_error(self):
        real_cloud, app.router.cloud = app.router.cloud, None  # never reach a paid API from a test
        app.router.local._avail = (9e18, False)  # pretend Ollama is down
        try:
            with self.assertRaises(EngineUnavailable):
                Agent(app, preset="chat", session_id="down").run("hello")
        finally:
            app.router.cloud, app.router.local._avail = real_cloud, (0.0, False)

    def test_protocols(self):
        self.assertIn("night", all_protocols(app))
        self.assertEqual(match_protocol(app, "Night"), "night")
        agent = Agent(app, preset="lite", session_id="proto")
        self.assertIn("Focus mode engaged", run_protocol(agent, "focus"))


class Memory(unittest.TestCase):
    def test_remember_search_forget(self):
        fid = app.memory.add_fact("The user likes green tea.")
        self.assertEqual(app.memory.add_fact("The user likes green tea."), fid)  # no duplicates
        self.assertEqual([r["id"] for r, _ in app.memory.search_facts("tea")], [fid])
        self.assertTrue(app.memory.forget(fid))
        self.assertEqual(app.memory.search_facts("tea"), [])


class WebServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app, "", True))
        cls.httpd.daemon_threads = True
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def call(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=10)
        h = dict(headers or {})
        if body is not None:
            h.setdefault("Content-Type", "application/json")
        c.request(method, path, json.dumps(body) if body is not None else None, h)
        r = c.getresponse()
        try:
            return r.status, r.read()
        finally:
            c.close()

    def test_pages_and_api(self):
        self.assertEqual(self.call("GET", "/api/health")[0], 200)
        status, page = self.call("GET", "/")
        self.assertEqual((status, page.decode()), (200, GUI_HTML))
        info = json.loads(self.call("GET", "/api/info")[1])
        self.assertEqual(info["default_preset"], "lite")
        self.assertEqual(self.call("POST", "/api/memory", {"text": "web fact"})[0], 200)
        facts = json.loads(self.call("GET", "/api/memory")[1])
        self.assertIn("web fact", [f["text"] for f in facts])

    def test_cross_site_requests_are_refused(self):
        self.assertEqual(self.call("POST", "/api/memory", None, {"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.call("GET", "/api/info", None, {"Host": "evil.example"})[0], 403)


class PhoneAccess(unittest.TestCase):
    """The server as `gui --host 0.0.0.0` runs it: a token is required and other devices cannot approve risky actions."""
    TOKEN = "test-token-123"

    def serve(self, from_phone):
        handler = make_handler(app, self.TOKEN, False)
        if from_phone:
            handler._is_local = lambda self: False  # pretend every client is another device on the network
        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        httpd.daemon_threads = True
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        return httpd.server_address[1]

    def call(self, port, method, path, body=None, token=True):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
        h = {"Authorization": f"Bearer {self.TOKEN}"} if token else {}
        if body is not None:
            h["Content-Type"] = "application/json"
        c.request(method, path, json.dumps(body) if body is not None else None, h)
        r = c.getresponse()
        try:
            return r.status, r.read()
        finally:
            c.close()

    def test_token_is_only_for_network_mode(self):
        self.assertEqual(access_token(app, loopback=True), "")  # PC-only: plain bookmarks keep working
        token = access_token(app, loopback=False)
        self.assertGreaterEqual(len(token), 20)
        self.assertEqual(access_token(app, loopback=False), token)  # the same link every time
        self.assertEqual(app.cfg.get("server.token"), token)
        self.assertEqual(access_token(app, loopback=True), "")  # still none for PC-only after a token exists

    def test_token_is_required(self):
        port = self.serve(from_phone=True)
        self.assertEqual(self.call(port, "GET", "/api/info", token=False)[0], 401)
        self.assertEqual(self.call(port, "GET", "/api/info")[0], 200)

    def test_phone_link_is_shown_only_on_the_pc(self):
        pc = json.loads(self.call(self.serve(from_phone=False), "GET", "/api/info")[1])
        phone = json.loads(self.call(self.serve(from_phone=True), "GET", "/api/info")[1])
        self.assertIsNone(phone["phone_url"])
        if lan_ip():
            self.assertTrue(pc["phone_url"].startswith(f"http://{lan_ip()}:"))
            self.assertTrue(pc["phone_url"].endswith(f"/?token={self.TOKEN}"))
        else:
            self.assertIsNone(pc["phone_url"])

    def test_phone_cannot_approve_risky_actions(self):
        port = self.serve(from_phone=True)
        self.assertEqual(self.call(port, "POST", "/api/approve", {"id": "x", "decision": "allow"})[0], 403)
        model = FakeModel(
            ChatResult(tool_calls=[{"id": "1", "name": "write_file", "arguments": {"path": "from_phone.txt", "content": "x"}}],
                       engine="fake", model="fake"),
            ChatResult(content="I could not do that.", engine="fake", model="fake"))
        real, app.router.chat = app.router.chat, model
        try:
            stream = self.call(port, "POST", "/api/chat", {"message": "write a file", "preset": "code"})[1]
        finally:
            app.router.chat = real
        events = [json.loads(line[6:]) for line in stream.decode().split("\n\n") if line.startswith("data: ")]
        ended = [e for e in events if e["type"] == "tool_end"]
        self.assertEqual(len(ended), 1)
        self.assertFalse(ended[0]["ok"])
        self.assertIn("denied", ended[0]["preview"])
        self.assertFalse(Path("from_phone.txt").exists())
        self.assertFalse([e for e in events if e["type"] == "approval"])  # nothing was queued for anyone to approve


if __name__ == "__main__":
    unittest.main()
