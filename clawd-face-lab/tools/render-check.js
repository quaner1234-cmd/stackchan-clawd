// Headless render check for clawd-face-lab/index.html
// Uses the real Edge (headless) to rasterise the canvas at 320x240 and save PNGs.
// No npm install: CDP over a raw WebSocket implemented with node's net/http.
const { spawn } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");
const http = require("http");
const net = require("net");
const crypto = require("crypto");

const EDGE = process.env.CLAWD_EDGE_PATH || "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
// Resolve the page relative to this script instead of a hard-coded machine path.
const FILE = "file:///" + path.resolve(__dirname, "..", "index.html").replace(/\\/g, "/");
const OUT = path.resolve(__dirname, "render-check");
const PROFILE = path.join(os.tmpdir(), "clawd-edge-prof");
const PORT = 9333;

fs.mkdirSync(OUT, { recursive: true });

const sleep = (ms) => new Promise(r => setTimeout(r, ms));

function httpGet(url) {
  return new Promise((res, rej) => {
    http.get(url, r => {
      let d = "";
      r.on("data", c => d += c);
      r.on("end", () => res(JSON.parse(d)));
    }).on("error", rej);
  });
}

/* ---- minimal websocket client (no deps) ---- */
function wsConnect(wsUrl) {
  const u = new URL(wsUrl);
  const key = crypto.randomBytes(16).toString("base64");
  const sock = net.connect(Number(u.port), u.hostname);
  let handshakeDone = false;
  let buf = Buffer.alloc(0);
  const handlers = [];

  const ready = new Promise((res, rej) => {
    sock.on("error", rej);
    sock.on("connect", () => {
      sock.write(
        `GET ${u.pathname}${u.search} HTTP/1.1\r\n` +
        `Host: ${u.host}\r\n` +
        `Upgrade: websocket\r\nConnection: Upgrade\r\n` +
        `Sec-WebSocket-Key: ${key}\r\nSec-WebSocket-Version: 13\r\n\r\n`
      );
    });
    const onData = (d) => {
      if (handshakeDone) return;
      buf = Buffer.concat([buf, d]);
      const i = buf.indexOf("\r\n\r\n");
      if (i < 0) return;
      handshakeDone = true;
      buf = buf.slice(i + 4);
      sock.off("data", onData);
      res();
    };
    sock.on("data", onData);
  });

  sock.on("data", chunk => {
    buf = Buffer.concat([buf, chunk]);
    while (buf.length >= 2) {
      const b1 = buf[0], b2 = buf[1];
      const opcode = b1 & 0x0f;
      let len = b2 & 0x7f, off = 2;
      if (len === 126) { if (buf.length < 4) return; len = buf.readUInt16BE(2); off = 4; }
      else if (len === 127) { if (buf.length < 10) return; len = Number(buf.readBigUInt64BE(2)); off = 10; }
      if (buf.length < off + len) return;
      const payload = buf.slice(off, off + len);
      buf = buf.slice(off + len);
      if (opcode === 1) handlers.forEach(h => h(payload.toString("utf8")));
    }
  });

  return {
    ready,
    on: (fn) => handlers.push(fn),
    send(obj) {
      const data = Buffer.from(JSON.stringify(obj), "utf8");
      const mask = crypto.randomBytes(4);
      let header;
      if (data.length < 126) header = Buffer.from([0x81, 0x80 | data.length]);
      else if (data.length < 65536) {
        header = Buffer.alloc(4); header[0] = 0x81; header[1] = 0x80 | 126; header.writeUInt16BE(data.length, 2);
      } else {
        header = Buffer.alloc(10); header[0] = 0x81; header[1] = 0x80 | 127; header.writeBigUInt64BE(BigInt(data.length), 2);
      }
      const masked = Buffer.alloc(data.length);
      for (let i = 0; i < data.length; i++) masked[i] = data[i] ^ mask[i % 4];
      sock.write(Buffer.concat([header, mask, masked]));
    },
    close: () => sock.destroy(),
  };
}

(async () => {
  const args = [
    "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
    `--remote-debugging-port=${PORT}`, "--remote-allow-origins=*",
    "--no-first-run", "--no-default-browser-check", "--disable-features=msEdgeFirstRunExperience",
    `--user-data-dir=${PROFILE}`,
    "--window-size=1400,900",
    "about:blank",
  ];
  const proc = spawn(EDGE, args, { stdio: ["ignore", "pipe", "pipe"] });
  let stderrBuf = "";
  proc.stderr.on("data", d => { stderrBuf += d.toString(); });
  proc.stdout.on("data", d => { stderrBuf += d.toString(); });

  // Fixed port -> discover via HTTP instead of parsing stderr.
  let ver = null;
  for (let i = 0; i < 80 && !ver; i++) {
    await sleep(250);
    try { ver = await httpGet(`http://127.0.0.1:${PORT}/json/version`); } catch { /* not up yet */ }
  }
  if (!ver) { proc.kill(); throw new Error("devtools never came up; stderr=" + stderrBuf.slice(-600)); }
  console.log("browser:", ver.Browser);

  const ws = wsConnect(ver.webSocketDebuggerUrl);
  await ws.ready;

  let id = 0;
  const pending = new Map();
  ws.on(raw => {
    let m;
    try { m = JSON.parse(raw); } catch { return; }
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
  });
  const cmd = (method, params = {}, sessionId) => new Promise(res => {
    const i = ++id;
    pending.set(i, res);
    ws.send({ id: i, method, params, sessionId });
  });

  const { result: target } = await cmd("Target.createTarget", { url: "about:blank" });
  const { result: sess } = await cmd("Target.attachToTarget", { targetId: target.targetId, flatten: true });
  const sid = sess.sessionId;

  await cmd("Page.enable", {}, sid);
  await cmd("Runtime.enable", {}, sid);
  await cmd("Emulation.setDeviceMetricsOverride",
    { width: 1400, height: 900, deviceScaleFactor: 1, mobile: false }, sid);

  const logs = [];
  ws.on(raw => {
    let m; try { m = JSON.parse(raw); } catch { return; }
    if (m.method === "Runtime.consoleAPICalled" || m.method === "Runtime.exceptionThrown") logs.push(JSON.stringify(m.params).slice(0, 300));
  });

  const evaluate = async (expr) => {
    const r = await cmd("Runtime.evaluate",
      { expression: expr, awaitPromise: true, returnByValue: true }, sid);
    if (r.result?.exceptionDetails) throw new Error("JS error: " + JSON.stringify(r.result.exceptionDetails).slice(0, 500));
    return r.result?.result?.value;
  };

  const shot = async (name, clip) => {
    const args2 = { format: "png", captureBeyondViewport: true };
    if (clip) args2.clip = { ...clip, scale: 1 };
    const r = await cmd("Page.captureScreenshot", args2, sid);
    if (!r.result?.data) throw new Error("no screenshot data for " + name);
    const p = path.join(OUT, name);
    fs.writeFileSync(p, Buffer.from(r.result.data, "base64"));
    return p;
  };

  /* ---- 1. load the page, animated mode ---- */
  await cmd("Page.navigate", { url: FILE }, sid);
  await sleep(1500);
  const ready = await evaluate("!!(window.__clawd && window.__clawd.ready)");
  console.log("page ready:", ready);
  if (!ready) {
    const errs = await evaluate("window.__err || 'none'");
    console.log("errors:", errs);
  }

  /* ---- 2. canvas geometry is exactly 320x240 ---- */
  const geom = await evaluate(`(() => {
    const c = document.getElementById('frame');
    return { w: c.width, h: c.height,
             cssW: getComputedStyle(c).width,
             smoothing: c.getContext('2d').imageSmoothingEnabled };
  })()`);
  console.log("canvas geometry:", JSON.stringify(geom));

  /* ---- 3. animation actually advances (blink / mouth / breath produce different pixels) ---- */
  const anim = await evaluate(`(async () => {
    const c = document.getElementById('frame');
    const g = c.getContext('2d');
    const grab = () => g.getImageData(0,0,320,240).data;
    const frames = [];
    for (let i=0;i<8;i++){ frames.push(grab()); frames.push(grab()); await new Promise(r=>setTimeout(r,120)); }
    // compare consecutive frames
    let changed = 0;
    for (let i=1;i<frames.length;i++){
      let diff = 0;
      const a = frames[i-1], b = frames[i];
      for (let p=0;p<a.length;p+=4) if (a[p]!==b[p]||a[p+1]!==b[p+1]||a[p+2]!==b[p+2]) { diff++; if (diff>50) break; }
      if (diff > 50) changed++;
    }
    return { samples: frames.length, changedTransitions: changed };
  })()`);
  console.log("animation:", JSON.stringify(anim));

  /* ---- 4. per-emotion deterministic frames ---- */
  const frames = [
    ["emotion-neutral-standby.png", "#f=neutral&s=standby&t=0&bare=1"],
    ["emotion-happy-standby.png",   "#f=happy&s=standby&t=0&bare=1"],
    ["emotion-angry-speak.png",     "#f=angry&s=speaking&t=190&bare=1"],
    ["emotion-sad-standby.png",    "#f=sad&s=standby&t=0&bare=1"],
    ["emotion-doubt-listening.png", "#f=doubt&s=listening&t=0&bare=1"],
    ["emotion-sleepy-standby.png", "#f=sleepy&s=standby&t=1200&bare=1"],
  ];
  for (const [name, hash] of frames) {
    await cmd("Page.navigate", { url: FILE + hash }, sid);
    await sleep(700);
    const box = await evaluate(`(() => { const r = document.getElementById('frame').getBoundingClientRect();
      return {x:Math.round(r.x), y:Math.round(r.y), width:Math.round(r.width), height:Math.round(r.height)}; })()`);
    const p = await shot(name, { x: box.x, y: box.y, width: box.width, height: box.height });
    const sz = fs.statSync(p).size;
    console.log("saved", name, box.width + "x" + box.height, sz + "B");
  }

  /* ---- 5. full page (UI + table) ---- */
  await cmd("Page.navigate", { url: FILE }, sid);
  await sleep(1200);
  console.log("saved full-page.png", fs.statSync(await shot("full-page.png")).size + "B");

  /* ---- 6. console errors? ---- */
  console.log("console events:", logs.length ? logs.slice(0, 5) : "none");

  ws.close();
  proc.kill();
  console.log("DONE");
  process.exit(0);
})().catch(e => { console.error("FAIL:", e.message); process.exit(1); });
