// Record demo GIFs for the README against the isolated recording instance.
//
//   pwsh -File tools/record_instance.ps1 start
//   node tools/record_demos.mjs
//
// Emits .recording/videos/<name>.webm plus videos/meta.json describing each
// node's on-screen box so the encoder can crop to it.
//
// Demo content is emitted by the throwaway /cn_recorder helper in the ComfyUI
// instance. Those lines travel the real capture path (stdout proxy -> ring
// buffer -> SSE -> canvas widget); only the *source* of the text is synthetic,
// so the recorded behaviour is the node's real behaviour.

import { createRequire } from 'node:module';
import { mkdirSync, writeFileSync, readFileSync, rmSync, existsSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const PW_ROOT = process.env.PLAYWRIGHT_ROOT ?? 'E:/projects/VF-ComfyUI-Layout-Templates';
const { chromium } = createRequire(`${PW_ROOT}/package.json`)('playwright');

const BASE = process.env.CN_BASE ?? 'http://127.0.0.1:8198';
const REC = join(fileURLToPath(new URL('../.recording/', import.meta.url)));
const VIDEO_DIR = join(REC, 'videos');
const ONLY = process.env.CN_ONLY ? process.env.CN_ONLY.split(',') : null;

const VIEW = { width: 900, height: 660 };
const NODE_SIZE = [780, 470];
// Playwright lays the page out in the top-left VIEW region of the video and
// pads the rest, so recordVideo.size must equal the viewport. Rendering still
// happens at deviceScaleFactor 2 and gets resampled down, which supersamples
// the text; asking for a 2x video instead just adds grey padding to crop.
const VIDEO_SCALE = 1;   // video pixels per CSS pixel in the content region

// Chrome that would otherwise overlap the node in frame.
const HIDE_CSS = `
  #comfy-menu-bare, .comfy-menu, .p-buttongroup, .dock-menu,
  .comfy-app-header, .litegraph.extra, [class*="status-bar"] { display: none !important; }
`;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// --- demo content -----------------------------------------------------------
// Levels are assigned server-side by capture.classify() from the line text,
// so these use the same prefixes ComfyUI itself logs with.
const INFO = (t, hold = 0.28) => ({ text: t, hold });
const ERR = (t, hold = 0.45) => ({ text: t, hold });

const SESSION_A = [
  INFO('[INFO] Loading diffusion model from diffusion_models/flux1-dev-fp8.safetensors'),
  INFO('[INFO] Loaded diffusion model in 4.21s'),
  INFO('[INFO] Loading VAE from vae/ae.safetensors'),
  INFO('[WARNING] Deprecated flag --highvram in config, ignoring'),
  INFO('[INFO] Prompt received (7 nodes)'),
  ERR('[ERROR] Connection error while fetching model metadata'),
  INFO('[INFO] Sampler: euler, scheduler: simple, steps: 20'),
  INFO('[INFO] Prompt executed in 8.43 seconds'),
];

const SESSION_B = [
  INFO('[INFO] Loading LoRA from loras/detail.safetensors', 0.3),
  INFO('[WARNING] LoRA not applied to conditioning', 0.3),
  ERR('[ERROR] CUDA out of memory while decoding batch', 0.45),
  INFO('[INFO] Falling back to tiled vae decode', 0.3),
];

const LOADING_LINES = [
  INFO('[INFO] Total VRAM 24576MB, free 21980MB', 0.3),
  INFO('[INFO] Loading checkpoint shards', 0.3),
  INFO('[WARNING] Skipping safetensors metadata for shard 2', 0.3),
  INFO('[INFO] CLIP text encoder loaded', 0.3),
];

// Enough lines that dropping the buffer cap is visibly a trim, not a no-op.
// The widget clamps the cap to >= 100, so the session has to exceed 100 for
// the trim to show at all.
const LONG_SESSION = Array.from({ length: 140 }, (_, i) => {
  if (i % 35 === 7) return INFO('[WARNING] Free VRAM dropped below 2GB', 0.05);
  if (i === 99) return ERR('[ERROR] Slow response from model host', 0.06);
  return INFO(`[INFO] Sampling step ${i + 1}/140`, 0.04);
});

// --- helpers ----------------------------------------------------------------
async function emit(page, lines) {
  const res = await page.request.post(`${BASE}/cn_recorder/emit`, { data: { lines } });
  if (!res.ok()) throw new Error(`emit failed: ${res.status()}`);
}

async function progress(page, opts) {
  const res = await page.request.post(`${BASE}/cn_recorder/progress`, { data: opts });
  if (!res.ok()) throw new Error(`progress failed: ${res.status()}`);
}

async function flush(page, text, hold = 0.35) {
  const res = await page.request.post(`${BASE}/cn_recorder/flush`, { data: { text, hold } });
  if (!res.ok()) throw new Error(`flush failed: ${res.status()}`);
}

async function prepare(page) {
  await page.goto(BASE, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => window.app && window.app.graph && window.app.canvas, null, { timeout: 120000 });
  await page.addStyleTag({ content: HIDE_CSS });
  await page.evaluate(() => window.app.loadGraphData({ last_node_id: 0, last_link_id: 0, nodes: [], links: [], groups: [], config: {}, extra: {}, version: 0 }));
  await page.waitForTimeout(1200);
}

// Hide every ancestor's siblings so only the node subtree renders. The Vue
// frontend's tab bar, side rail and run toolbar float above the canvas and
// would otherwise sit on top of the node in every frame.
async function isolate(page) {
  return page.evaluate(() => {
    const root = document.querySelector('.cn-root');
    if (!root) return 'no-widget';
    // Climb to the node container so its title bar stays in frame.
    let node = root;
    for (let i = 0; i < 8 && node.parentElement; i++) {
      node = node.parentElement;
      if (String(node.className || '').includes('lg-node')) break;
    }
    let child = node;
    let parent = node.parentElement;
    let hidden = 0;
    while (parent && parent !== document.body) {
      for (const sib of parent.children) {
        if (sib !== child) { sib.style.setProperty('display', 'none', 'important'); hidden++; }
      }
      child = parent;
      parent = parent.parentElement;
    }
    return `isolated(${hidden} hidden)`;
  });
}

async function addNode(page) {
  const via = await page.evaluate(() => {
    const node = window.LiteGraph.createNode('ConsoleLogViewer');
    if (!node) return 'createNode-failed';
    window.app.graph.add(node);
    node.setSize([780, 470]);
    node.pos = [-40, -60];
    window.app.graph.setDirtyCanvas(true, true);
    window.app.canvas.setZoom(1);
    window.app.canvas.ds.offset = [70, 96];
    return 'programmatic';
  });
  await page.waitForTimeout(2500);
  const iso = await isolate(page);
  console.log(`  ${iso}`);
  await page.waitForTimeout(900);
  return via;
}

// Locate the node's rendered box (title bar included) in CSS pixels.
async function nodeRect(page) {
  return page.evaluate(() => {
    const w = document.querySelector('.cn-root');
    if (!w) return null;
    let el = w;
    for (let i = 0; i < 6 && el.parentElement; i++) {
      el = el.parentElement;
      if (el.className && String(el.className).includes('lg-node')) break;
    }
    const r = el.getBoundingClientRect();
    return { x: Math.round(r.x), y: Math.round(r.y), width: Math.round(r.width), height: Math.round(r.height) };
  });
}

const widget = {
  root: '.cn-root',
  level: '.cn-bar select',
  filter: '.cn-bar input[placeholder*="filter"]',
  max: '.cn-bar input[type="number"]',
  pause: '.cn-bar button:text-is("pause")',
  follow: '.cn-bar button:text-is("follow")',
  clear: '.cn-bar button:text-is("clear")',
  state: '.cn-state',
  log: '.cn-log',
};

// Wait for the line count to stop changing, i.e. the SSE 'backlog' event has
// been applied. Waiting on state==='live' is not enough: onopen fires *before*
// the backlog arrives, so clearing immediately gets undone by it.
async function settle(page) {
  let last = -1;
  let stable = 0;
  for (let i = 0; i < 40; i++) {
    const n = await page.evaluate(() => document.querySelectorAll('.cn-line').length);
    if (n > 0 && n === last) {
      if (++stable >= 3) return n;
    } else {
      stable = 0;
    }
    last = n;
    await sleep(300);
  }
  return last;
}

// Wait for the widget to be live. `clear` empties the viewer so each demo
// stands alone; the server-side ring keeps accumulating across demos, so
// without this later demos open on the previous demo's leftovers.
async function ready(page, { clear = true } = {}) {
  await page.waitForSelector(widget.root, { timeout: 60000 });
  await page.waitForFunction(() => document.querySelector('.cn-state')?.textContent === 'live', null, { timeout: 60000 });
  if (clear) {
    await settle(page);
    for (let attempt = 0; attempt < 3; attempt++) {
      await page.click(widget.clear);
      await sleep(900);
      const n = await page.evaluate(() => document.querySelectorAll('.cn-line').length);
      if (n === 0) return;
    }
    throw new Error(`viewer would not clear (still ${n} lines)`);
  }
  await settle(page);
}

// --- demos ------------------------------------------------------------------
const DEMOS = [
  {
    clear: false,
    name: '01-live-log',
    caption: 'The viewer streams the server console onto the canvas, colour-coded by level.',
    async run(page) {
      await sleep(1400);
      await emit(page, SESSION_A);
      await sleep(1200);
    },
  },
  {
    name: '02-level-filter',
    caption: 'The level dropdown filters by severity without touching the stream.',
    async run(page) {
      await emit(page, LOADING_LINES);
      await sleep(1400);
      await page.selectOption(widget.level, 'WARN');
      await sleep(1500);
      await page.selectOption(widget.level, 'INFO');
      await sleep(1500);
      await page.selectOption(widget.level, 'ALL');
      await sleep(1200);
    },
  },
  {
    name: '03-regex-filter',
    caption: 'Filter by case-insensitive regex; an invalid pattern is flagged in place.',
    async run(page) {
      await emit(page, SESSION_B);
      await sleep(1500);
      await page.click(widget.filter);
      await page.type(widget.filter, 'lora|vae', { delay: 55 });
      await sleep(1800);
      await page.fill(widget.filter, 'lora|(');   // invalid on purpose
      await sleep(1400);
      await page.fill(widget.filter, '');          // clear again
      await sleep(1200);
    },
  },
  {
    name: '04-progress-replaces',
    caption: 'In-place progress updates replace the previous line instead of flooding the view.',
    async run(page) {
      await progress(page, { steps: 30, label: 'FETCH flux1-dev-fp8.safetensors', hold: 0.085 });
      await flush(page, '[INFO] Checkpoint downloaded, 16.9 GB', 0.5);
      await progress(page, { steps: 24, label: 'LOAD vae/ae.safetensors', hold: 0.085 });
      await flush(page, '[INFO] VAE ready in 2.10s', 0.5);
      await sleep(900);
    },
  },
  {
    name: '05-pause-and-resume',
    caption: 'Pause freezes rendering while the server keeps buffering; resuming replays the gap.',
    async run(page) {
      await sleep(600);
      await page.click(widget.pause);
      await sleep(700);
      await emit(page, [
        INFO('[INFO] Queued 3 further requests', 0.3),
        INFO('[INFO] Sampling step 8/20', 0.3),
        INFO('[INFO] Sampling step 12/20', 0.3),
      ]);
      await sleep(1300);
      await page.click(widget.pause);
      await sleep(1600);
      await emit(page, [INFO('[INFO] Resumed: caught up on 3 buffered lines', 0.5)]);
      await sleep(1200);
    },
  },
  {
    name: '06-follow-and-limits',
    caption: 'Follow keeps the newest line in view; the buffer limit trims from the top.',
    async run(page) {
      await emit(page, LONG_SESSION);
      await sleep(1400);
      await page.click(widget.follow);              // follow off
      await sleep(400);
      await page.evaluate(() => { const l = document.querySelector('.cn-log'); if (l) l.scrollTop = 0; });
      await sleep(1300);
      await page.click(widget.follow);              // follow back on
      await sleep(1100);
      // Drop the cap to its floor so ~40 of the 140 lines fall off the top.
      await page.fill(widget.max, '100');
      await page.dispatchEvent(widget.max, 'change');
      await sleep(1900);
    },
  },
];

// Only clear the clips we are about to re-record, and merge into any existing
// meta.json, so a single-demo re-run (CN_ONLY) does not discard the rest.
const META_PATH = join(VIDEO_DIR, 'meta.json');
mkdirSync(VIDEO_DIR, { recursive: true });

const targets = DEMOS.filter((d) => !ONLY || ONLY.includes(d.name));
if (existsSync(VIDEO_DIR)) {
  for (const d of targets) {
    const stale = join(VIDEO_DIR, `${d.name}.webm`);
    if (existsSync(stale)) rmSync(stale, { force: true });
  }
}

let previous = { demos: [] };
if (ONLY && existsSync(META_PATH)) {
  try { previous = JSON.parse(readFileSync(META_PATH, 'utf8')); } catch { }
}

const browser = await chromium.launch({ args: ['--disable-gpu', '--force-color-profile=srgb', '--hide-scrollbars'] });
const meta = [];
const report = [];

for (const demo of DEMOS) {
  if (ONLY && !ONLY.includes(demo.name)) continue;
  process.stdout.write(`\n=== ${demo.name} ===\n`);

  const t0 = Date.now();          // video starts with the context
  const context = await browser.newContext({
    viewport: VIEW,
    deviceScaleFactor: 2,
    recordVideo: { dir: VIDEO_DIR, size: VIEW },
  });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));

  let addVia = 'n/a';
  try {
    await prepare(page);
    addVia = await addNode(page);

    // Readiness (and any clear) must happen BEFORE the lead-in is measured,
    // otherwise the encoder trims to a point where the viewer still holds the
    // previous demo's backlog.
    await ready(page, { clear: demo.clear !== false });

    const rect = await nodeRect(page);
    console.log(`  node rect ${JSON.stringify(rect)}`);

    // Page load plus the settle/clear is dead air; the encoder trims it.
    const leadIn = (Date.now() - t0) / 1000;
    console.log(`  added via ${addVia}; lead-in ${leadIn.toFixed(1)}s`);

    await demo.run(page);

    // Confirm the widget actually received data before trusting the frames.
    const state = await page.evaluate(() => ({
      lines: document.querySelectorAll('.cn-line').length,
      status: document.querySelector('.cn-state')?.textContent ?? null,
    }));
    console.log(`  final: ${state.lines} lines, state=${state.status}`);
    if (state.lines === 0) report.push(`${demo.name}: NO LINES RENDERED`);
    if (state.status !== 'live') report.push(`${demo.name}: state=${state.status}`);

    const video = page.video();
    await context.close();
    const path = await video.path();

    // Playwright names the file <context-id>.webm; rename to the demo name.
    const finalPath = join(VIDEO_DIR, `${demo.name}.webm`);
    const { renameSync } = await import('node:fs');
    renameSync(path, finalPath);
    meta.push({ name: demo.name, caption: demo.caption, video: finalPath, rect, addVia, lines: state.lines, errors, leadIn });
    console.log(`  saved ${demo.name}.webm`);
  } catch (err) {
    report.push(`${demo.name}: ${err.message}`);
    console.error(`  FAILED ${err.message}`);
    try { await context.close(); } catch { }
  }
}

// Keep every previously recorded demo, replacing only what this run touched.
const merged = new Map((previous.demos ?? []).map((d) => [d.name, d]));
for (const d of meta) merged.set(d.name, d);
const ordered = DEMOS.map((d) => merged.get(d.name)).filter(Boolean);

writeFileSync(META_PATH, JSON.stringify({
  view: VIEW, videoScale: VIDEO_SCALE, nodeSize: NODE_SIZE, demos: ordered,
}, null, 2));
await browser.close();

console.log('\n================ REPORT ================');
if (report.length === 0) console.log('all demos recorded with live data');
else report.forEach((r) => console.log('ISSUE:', r));
for (const m of meta) console.log(`  ${m.name}: ${m.lines} lines, ${m.addVia}, rect ${JSON.stringify(m.rect)}`);
