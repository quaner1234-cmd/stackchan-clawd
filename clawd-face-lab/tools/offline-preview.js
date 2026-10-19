// Local Canvas preview. No browser, web server, network or device access.
// Run with NODE_PATH pointing at the existing Codex bundled node_modules.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const { createCanvas, ImageData } = require('@napi-rs/canvas');

const root = path.resolve(__dirname, '..');
const out = path.join(__dirname, 'render-check');
fs.mkdirSync(out, { recursive: true });

function load(hash = '') {
  const source = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
  const elements = [];
  let now = 0;
  let raf;
  const intervals = new Map();
  class Element {
    constructor(tag = 'div') {
      this.tagName = tag; this.dataset = {}; this.style = {}; this.children = [];
      this.listeners = {}; this.attributes = {}; this.textContent = '';
      const classes = new Set();
      this.classList = {
        add: x => classes.add(x), remove: x => classes.delete(x), contains: x => classes.has(x),
        toggle: (x, on = !classes.has(x)) => { on ? classes.add(x) : classes.delete(x); return on; },
      };
      elements.push(this);
    }
    appendChild(child) { this.children.push(child); }
    setAttribute(key, value) { this.attributes[key] = String(value); }
    getAttribute(key) { return this.attributes[key] ?? null; }
    addEventListener(key, fn) { this.listeners[key] = fn; }
    getBoundingClientRect() { return { left: 0, top: 0, width: 640, height: 480 }; }
    click() { this.onclick?.(); }
  }
  const canvas = createCanvas(320, 240);
  const ids = {};
  for (const match of source.matchAll(/<([a-z][\w-]*)\b([^>]*)>/gi)) {
    const attrs = match[2];
    const id = attrs.match(/\bid="([^"]+)"/)?.[1];
    const data = [...attrs.matchAll(/\bdata-([\w-]+)="([^"]*)"/g)];
    if (!id && !data.length) continue;
    const el = new Element(match[1]);
    if (id) { el.id = id; ids[id] = el; }
    for (const [, key, value] of data) el.dataset[key] = value;
  }
  Object.assign(ids.frame, {
    width: 320, height: 240,
    getContext: (...args) => canvas.getContext(...args),
    toDataURL: (...args) => canvas.toDataURL(...args),
  });
  const querySelectorAll = selector => {
    const match = selector.match(/^\[data-([\w-]+)(?:="([^"]*)")?\]$/);
    if (!match) throw new Error('Unsupported fixture selector: ' + selector);
    return elements.filter(e => match[1] in e.dataset && (match[2] === undefined || e.dataset[match[1]] === match[2]));
  };
  const document = {
    body: new Element('body'), title: '',
    getElementById: id => { if (!ids[id]) throw new Error('Missing HTML element: ' + id); return ids[id]; },
    createElement: tag => new Element(tag), querySelectorAll,
    querySelector: selector => querySelectorAll(selector)[0],
  };
  const context = vm.createContext({
    document, location: { hash }, performance: { now: () => now },
    ImageData, URLSearchParams, console, window: {},
    requestAnimationFrame: fn => { raf = fn; },
    setInterval: fn => { const id = intervals.size + 1; intervals.set(id, fn); return id; },
    clearInterval: id => intervals.delete(id),
  });
  vm.runInContext(source.match(/<script>([\s\S]*?)<\/script>/)[1], context, { filename: 'index.html' });
  const api = context.window.__clawd;
  const tick = t => { now = t; const fn = raf; raf = null; fn?.(t); };
  const save = name => fs.writeFileSync(path.join(out, name), canvas.toBuffer('image/png'));
  const pixels = () => Buffer.from(canvas.getContext('2d').getImageData(0, 0, 320, 240).data);
  return { api, canvas, ids, elements, intervals, tick, save, pixels, querySelectorAll, context };
}

function run() {
  if (process.argv.includes('--before')) {
    const app = load('#f=neutral&s=standby&t=0&bare=1');
    app.save('before.png');
    console.log('Saved before.png using offline Canvas.');
    return;
  }
  const report = { renderer: '@napi-rs/canvas (offline, not a browser)', checks: [], browserUI: 'UNVERIFIED', device: 'UNVERIFIED' };
  const pass = label => { report.checks.push(label); console.log('PASS', label); };
  const grabRegion = (app, x, y, w, h) => Buffer.from(app.canvas.getContext('2d').getImageData(x, y, w, h).data);
  const emotions = ['neutral', 'happy', 'angry', 'sad', 'doubt', 'sleepy'];
  const statuses = ['standby', 'listening', 'speaking'];
  const sheet = createCanvas(1000, 586);
  const sg = sheet.getContext('2d');
  sg.fillStyle = '#f0ece5'; sg.fillRect(0, 0, sheet.width, sheet.height);
  const hashes = new Set();
  emotions.forEach((emotion, i) => {
    for (const status of statuses) {
      const app = load(`#f=${emotion}&s=${status}&t=90&bare=1`);
      assert.equal(app.canvas.width, 320); assert.equal(app.canvas.height, 240);
      const pixels = app.pixels();
      hashes.add(require('node:crypto').createHash('sha256').update(pixels).digest('hex'));
      // Keep a clear margin at every edge; all pixels must be opaque.
      for (let y = 0; y < 240; y++) for (let x = 0; x < 320; x++) {
        const p = (y * 320 + x) * 4;
        assert.equal(pixels[p + 3], 255);
        if (x < 15 || x > 304 || y < 15 || y > 236) {
          assert.deepEqual([...pixels.subarray(p, p + 3)], [250, 247, 242], `${emotion}/${status} edge clipped`);
        }
      }
      app.save(`${emotion}-${status}.png`);
      if (status === 'standby') {
        const x = 10 + (i % 3) * 330, y = 12 + Math.floor(i / 3) * 287;
        sg.drawImage(app.canvas, x, y);
        sg.fillStyle = '#62584e'; sg.font = '13px sans-serif'; sg.fillText(emotion.toUpperCase(), x + 12, y + 266);
      }
    }
  });
  assert.equal(hashes.size, 18, 'Every emotion/status combination should be visually distinct');
  fs.writeFileSync(path.join(out, 'expressions.png'), sheet.toBuffer('image/png'));
  pass('18 emotion/status combinations render at 320x240, opaque, distinct, with safe margins');

  for (const emotion of emotions) {
    const app = load(`#f=${emotion}&blink=1&bare=1`);
    app.api.frame(5100); const before = grabRegion(app, 80, 80, 160, 45);
    app.api.frame(5280); const closed = grabRegion(app, 80, 80, 160, 45);
    app.api.frame(5400); const after = grabRegion(app, 80, 80, 160, 45);
    assert.notDeepEqual(before, closed, `${emotion} does not blink`);
    assert.deepEqual(before, after, `${emotion} does not reopen`);
    if (emotion === 'neutral') app.save('blink-open.png');
    app.api.frame(5280);
    if (emotion === 'neutral') app.save('blink-closed.png');
  }
  pass('Blink visibly closes and reopens in all six emotions, including happy eyes');

  const speech = load('#f=neutral&s=speaking&bare=1');
  const mouths = [0, 45, 90, 225, 300].map(t => { speech.api.frame(t); return grabRegion(speech, 142, 120, 36, 42); });
  assert.notDeepEqual(mouths[0], mouths[2]); assert.deepEqual(mouths[0], mouths[4]);
  assert.notDeepEqual(mouths[1], mouths[2]);
  speech.api.frame(90); speech.save('speaking-open.png');
  speech.api.frame(300); speech.save('speaking-closed.png');
  speech.api.set({ status: 'standby' }); speech.api.frame(90);
  assert.deepEqual(grabRegion(speech, 142, 120, 36, 42), mouths[0]);
  pass('Speech mouth opens, interpolates, closes, and returns to neutral when stopped');

  const breath = load('#f=neutral&breath=1&bare=1');
  breath.api.frame(1650); const up = breath.pixels();
  breath.api.frame(4950); const down = breath.pixels();
  const eyeY = data => {
    const ys = [];
    for (let y = 70; y < 130; y++) if (data[(y * 320 + 100) * 4] < 80) ys.push(y);
    return (ys[0] + ys[ys.length - 1]) / 2;
  };
  const bodyY = data => {
    for (let y = 30; y < 70; y++) if (data[(y * 320 + 160) * 4] < 240) return y;
  };
  assert.equal(eyeY(up) - bodyY(up), eyeY(down) - bodyY(down));
  assert.equal(bodyY(up) - bodyY(down), 4);
  pass('Breathing moves body and eyes together through a 4px range');

  const app = load(); app.tick(0);
  let wall = 0;
  const advance = ms => { for (let elapsed = 0; elapsed < ms; elapsed += 20) { wall += 20; app.tick(wall); } };
  const click = (attr, value) => app.querySelectorAll(`[data-${attr}="${value}"]`)[0].click();
  click('k', 'happy'); const start = app.pixels();
  advance(140); const halfway = app.pixels(); advance(160); const end = app.pixels();
  assert.notDeepEqual(start, halfway); assert.notDeepEqual(halfway, end);
  assert.equal(app.api.state.emotion, 'happy');
  for (const emotion of emotions) {
    click('k', emotion); advance(300);
    assert.equal(app.api.state.emotion, emotion);
    assert.equal(app.querySelectorAll('[data-k]').filter(b => b.getAttribute('aria-pressed') === 'true').length, 1);
  }
  statuses.forEach(status => { click('s', status); assert.equal(app.api.state.status, status); });
  click('z', '1'); assert.equal(app.ids.frame.style.width, '320px');
  click('z', '3'); assert.equal(app.ids.frame.style.width, '960px');
  app.ids.bBlink.click(); assert.equal(app.api.state.blinkOn, false); assert.equal(app.ids.bBlink.textContent, '关');
  app.ids.bBreath.click(); assert.equal(app.api.state.breathOn, false);
  app.ids.bPause.click(); const paused = app.pixels(), pausedAt = app.api.state.now;
  advance(600); assert.deepEqual(app.pixels(), paused); assert.equal(app.api.state.now, pausedAt);
  app.ids.bPause.click(); advance(100); assert.equal(app.api.state.now, pausedAt + 100);
  pass('Control handlers: six emotions with interpolation, three statuses, scales, toggles, pause/resume');

  click('k', 'sad'); advance(300); app.ids.bPet.click(); advance(300);
  assert.equal(app.api.state.emotion, 'happy'); app.save('pet.png');
  app.ids.bPet.click(); advance(2700); assert.equal(app.api.state.emotion, 'sad');
  app.ids.bPet.click(); click('k', 'doubt'); advance(2800); assert.equal(app.api.state.emotion, 'doubt');
  app.ids.frame.listeners.click(); assert.equal(app.api.state.emotion, 'happy');
  app.ids.bAuto.click(); assert.equal(app.intervals.size, 1);
  for (let i = 0; i < 6; i++) [...app.intervals.values()][0]();
  assert.equal(app.api.state.emotion, 'sleepy');
  app.ids.bAuto.click(); assert.equal(app.intervals.size, 0);
  app.ids.bAuto.click(); app.ids.bAuto.click(); assert.equal(app.intervals.size, 0);
  pass('Pet restores the previous emotion; manual selection wins; demo timer starts/stops cleanly');

  app.ids.frame.listeners.pointermove({ clientX: 640, clientY: 480 }); advance(300);
  assert(app.api.state.look.x > 3 && app.api.state.look.x <= 4);
  app.ids.frame.listeners.pointerleave(); advance(600); assert(Math.abs(app.api.state.look.x) < .01);
  app.ids.frame.listeners.pointermove({ clientX: 0, clientY: 0 });
  app.ids.frame.listeners.pointerup({ pointerType: 'touch' }); assert.equal(app.api.state.gaze.x, 0);
  click('theme', 'night'); assert.equal(app.api.state.theme, 'night');
  assert.deepEqual([...app.pixels().subarray(0, 3)], [36, 35, 33]);
  app.save('night.png');
  pass('Gaze follows/returns, touch releases gaze, night theme renders');

  const a = load('#f=doubt&s=speaking&t=90&bare=1'), b = load('#f=doubt&s=speaking&t=90&bare=1');
  assert.deepEqual(a.pixels(), b.pixels());
  const invalid = load('#f=unknown&s=unknown&t=NaN&z=999&theme=__proto__');
  assert.equal(invalid.api.state.emotion, 'neutral'); assert.equal(invalid.api.state.theme, 'cream');
  invalid.api.set({ emotion: 'unknown', status: 'unknown', theme: '__proto__', zoom: 999 });
  assert.equal(invalid.api.state.zoom, 2); // invalid hash zoom leaves the default
  pass('Capture is deterministic and invalid capture/API options keep valid defaults');

  const framesDir = path.join(out, 'motion-frames'); fs.mkdirSync(framesDir, { recursive: true });
  const motion = load(); motion.tick(0); let motionWall = 0;
  for (let i = 0; i < 150; i++) {
    if (i === 35) motion.api.set({ status: 'speaking' });
    if (i === 78) motion.api.set({ emotion: 'happy', status: 'standby' });
    if (i === 105) motion.api.set({ emotion: 'doubt', status: 'listening' });
    if (i === 130) motion.ids.bPet.click();
    motionWall += 80; motion.tick(motionWall);
    fs.writeFileSync(path.join(framesDir, `${String(i).padStart(3, '0')}.png`), motion.canvas.toBuffer('image/png'));
  }
  fs.writeFileSync(path.join(out, 'offline-report.json'), JSON.stringify(report, null, 2));
  console.log(`Saved 18 states, expression sheet, 150 motion frames, and ${report.checks.length} passing check groups.`);
}

if (require.main === module) run();
module.exports = { load, out, run };
