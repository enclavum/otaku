/* The Web Worker that runs the whole application. Pyodide loads here,
   the wheels install here, and boot.main() BLOCKS here for the life of
   the session — which is the entire trick: a worker may block, and the
   page stays a terminal.

   The bridge given to boot.py reads keystrokes out of a SharedArrayBuffer
   ring the page writes into (Atomics.wait carries the blocking), sends
   output back with postMessage, and reads the terminal size from a
   second shared array the page updates on resize. */

importScripts("./pyodide/pyodide.js");

let meta = null; // Int32Array [head, tail] over the ring SAB
let data = null; // Uint8Array ring storage over the same SAB
let dims = null; // Int32Array [cols, rows]

const post = (type, payload) => postMessage({ type, ...payload });

const bridge = {
  read(ms) {
    let head = Atomics.load(meta, 0);
    const tail = Atomics.load(meta, 1);
    if (head === tail) {
      Atomics.wait(meta, 0, tail, ms < 0 ? Infinity : ms);
      head = Atomics.load(meta, 0);
      if (head === tail) return new Uint8Array(0);
    }
    const size = data.length;
    const length = (head - tail + size) % size;
    const bytes = new Uint8Array(length);
    for (let i = 0; i < length; i++) bytes[i] = data[(tail + i) % size];
    Atomics.store(meta, 1, head);
    Atomics.notify(meta, 1);
    return bytes;
  },
  write(s) {
    post("out", { data: s });
  },
  err(s) {
    console.error(s);
  },
  size() {
    return [Atomics.load(dims, 0), Atomics.load(dims, 1)];
  },
};

onmessage = async (event) => {
  const message = event.data;
  if (message.type !== "start") return;
  meta = new Int32Array(message.ring, 0, 2);
  data = new Uint8Array(message.ring, 8);
  dims = new Int32Array(message.dims);
  try {
    post("status", { text: "waking the snake…" });
    const py = await loadPyodide({ indexURL: "./pyodide/" });
    post("status", { text: "loading the packages…" });
    await py.loadPackage(["sqlite3", "ssl", "cryptography", "click", "httpx", "httpcore", "idna"], {
      messageCallback: () => {},
    });
    post("status", { text: "installing otaku…" });
    await py.loadPackage(message.wheels, { messageCallback: () => {} });
    py.registerJsModule("termbridge", bridge);
    py.FS.mkdirTree("/demo");
    for (const name of ["boot.py", "demo_script.py"]) {
      const source = await (await fetch(`./py/${name}`)).text();
      py.FS.writeFile(`/demo/${name}`, source);
    }
    post("status", { text: "" });
    py.runPython("import sys; sys.path.insert(0, '/demo'); import boot; boot.main()");
    post("exit", {});
  } catch (error) {
    console.error(error);
    post("crash", { text: String(error && error.message ? error.message : error) });
  }
};
