// The terminal demo's offline smoke: boot.py on Pyodide under Node, a
// scripted bridge standing in for the page — keystrokes are fed one
// batch per PROMPT (each "\x1b[5 q" the prompt session emits marks
// one), and the two terminal queries are answered the way
// scenarios/support/terminal.py answers them, so the theme probe and
// the screen ledger run their real paths.
//
//   cd demo/terminal && npm install pyodide@0.28.2 && node smoke.mjs
//
// Wheels come from the build's cache, the DEMO_CACHE demo/build_terminal.sh
// filled (it builds otaku's wheel and downloads prompt_toolkit's) — the
// site's demos/build.sh runs the build and then this over the same cache.
import { loadPyodide } from "pyodide";
import { readFileSync, readdirSync } from "fs";
import { fileURLToPath } from "url";
import { dirname, join } from "path";

const here = dirname(fileURLToPath(import.meta.url));
const cache = process.env.DEMO_CACHE;
if (!cache) throw new Error("set DEMO_CACHE to the directory demo/build_terminal.sh cached into");
const wheel = (prefix) => {
  const name = readdirSync(cache).find((f) => f.startsWith(prefix) && f.endsWith(".whl"));
  if (!name) throw new Error(`no ${prefix}*.whl in ${cache} — run demo/build_terminal.sh`);
  return join(cache, name);
};

const COLS = 100, ROWS = 30;
const strip = (s) => s.replace(/\x1b\][^\x07\x1b]*(\x07|\x1b\\)/g, "").replace(/\x1b[\[\]?]?[0-9;]*[ ]?[A-Za-z@`~]/g, "").replace(/\x1b[78=>]/g, "");

let raw = "";            // everything python wrote
let inbox = [];          // bytes queued for python
let answered = { cpr: 0, osc: 0 };
let sent = 0;
let smokeDone = false; // set when the harness itself ends the run
const started = Date.now();

// One batch per prompt, in prompt order. The demo cannot be quit —
// /bye answers a refusal — so once the last step's prompt has come
// back, the bridge ends the run itself with the SMOKE-DONE throw.
const PROMPT_STEPS = [
  { send: "Hello, river.\r", note: "play a line" },
  { send: "/regen\r", note: "regenerate" },
  { send: "/extract\r", note: "forced pass" },
  { send: "/bye\r", note: "quit refused" },
];

const count = (hay, needle) => hay.split(needle).length - 1;
const pushStr = (s) => { for (const b of Buffer.from(s, "utf8")) inbox.push(b); };

function autoRespond() {
  const cprs = count(raw, "\x1b[6n");
  while (answered.cpr < cprs) { pushStr("\x1b[24;1R"); answered.cpr++; }
  const oscs = count(raw, "\x1b]11;?\x07");
  while (answered.osc < oscs) { pushStr("\x1b]11;rgb:1a1a/1b1b/2626\x07"); answered.osc++; }
  const prompts = count(raw, "\x1b[5 q");
  if (sent < PROMPT_STEPS.length && prompts > sent) {
    console.log(`\n--- prompt ${prompts}: ${PROMPT_STEPS[sent].note} ---`);
    pushStr(PROMPT_STEPS[sent].send);
    sent++;
  }
}

const sleeper = new Int32Array(new SharedArrayBuffer(4));
const sleep = (ms) => Atomics.wait(sleeper, 0, 0, ms);

const bridge = {
  read(ms) {
    if (Date.now() - started > 150000) throw new Error("smoke watchdog: 150s elapsed");
    autoRespond();
    if (sent === PROMPT_STEPS.length && count(raw, "\x1b[5 q") > sent) {
      smokeDone = true; // the prompt after the refused /bye: the session lives on
      throw new Error("SMOKE-DONE");
    }
    if (!inbox.length && ms !== 0) {
      sleep(Math.min(ms < 0 ? 100 : ms, 100));
      autoRespond();
    }
    const chunk = Uint8Array.from(inbox);
    inbox = [];
    return chunk;
  },
  write(s) { raw += s; process.stdout.write(s); },
  err(s) { process.stderr.write("[stderr] " + s); },
  size() { return [COLS, ROWS]; },
};

const py = await loadPyodide({ stderr: (s) => process.stderr.write("[pyodide] " + s + "\n") });
console.log("pyodide up; loading packages…");
await py.loadPackage(["sqlite3", "ssl", "cryptography", "click", "httpx", "httpcore", "idna"], { messageCallback: () => {} });
await py.loadPackage([wheel("prompt_toolkit-"), wheel("wcwidth-"), wheel("otaku-")], {
  messageCallback: () => {},
});
py.registerJsModule("termbridge", bridge);
py.FS.mkdirTree("/demo");
py.FS.writeFile("/demo/boot.py", readFileSync(join(here, "boot.py")));
py.FS.writeFile("/demo/demo_script.py", readFileSync(join(here, "demo_script.py")));
console.log("running boot.main()…\n");

let done = false;
try {
  py.runPython("import sys; sys.path.insert(0, '/demo'); import boot; boot.main()");
  done = true; // unreachable while the demo refuses to quit, kept for honesty
} catch (e) {
  if (smokeDone) {
    done = true;
  } else {
    console.error("\n\nPYTHON CRASH:\n" + e.message);
  }
}

const text = strip(raw);
const checks = [
  ["banner/version", /otaku/i],
  ["landed in the sample", /The River That Forgot Its Name/],
  ["first continuation streamed", /speaking-tube carries a captain/],
  ["regenerate gave the alt take", /smell of deep stone|deciding about you|honestly wrong/],
  ["extraction closed a scene", /The Voice in the Culvert/],
  ["quitting refused", /This command is disabled in the demo/],
];
let failed = 0;
console.log("\n\n===== checks =====");
for (const [name, re] of checks) {
  const ok = re.test(text);
  if (!ok) failed++;
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}`);
}
console.log(`prompt batches sent: ${sent}/${PROMPT_STEPS.length}; python ${done ? "returned cleanly" : "CRASHED"}`);
if (!done || failed) {
  console.log("\nstripped tail:\n" + text.slice(-2500));
  process.exit(1);
}
console.log("SMOKE PASS");
