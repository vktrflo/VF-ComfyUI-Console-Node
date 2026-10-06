import { app } from '../../scripts/app.js';
import { api } from '../../scripts/api.js';

const NODE_NAME = 'ConsoleLogViewer';
const STREAM_PATH = '/console_node/log/stream';

const LEVEL_RANK = { ERROR: 3, WARN: 2, INFO: 1, STDOUT: 0, STDERR: 0 };
const THRESHOLDS = { ALL: -1, INFO: 1, WARN: 2, ERROR: 3 };

const STYLE = `
.cn-root { display:flex; flex-direction:column; width:100%; height:100%; font-family:ui-monospace, Consolas, monospace; font-size:11px; color:var(--fg-color,#ddd); max-height:max(160px, calc(var(--node-height, 100000px) - 72px)); }
/* Nodes 2.0 (Vue) mode: the renderer gives DOM widgets no height constraint
   and sizes the node from measured content, so an unclamped log (flex fill
   with overflow:auto) contributes its entire content height and balloons the
   node. The Vue renderer sets --node-height (px, including the title bar) on
   the node element; it inherits down to this element, so clamp the console to
   it minus the node chrome (36px header + body top, 36px body bottom +
   badges/footer, measured at the default node size) so the log scrolls
   instead. The 160px floor keeps the console usable when the node is dragged
   small. In legacy mode --node-height is absent and the fallback keeps the
   clamp inert (the legacy layout sizes the element itself). */
.cn-bar { display:flex; gap:4px; padding:4px 4px 2px 4px; flex-wrap:wrap; align-items:center; }
.cn-bar input, .cn-bar select, .cn-bar button { font-size:11px; background:rgba(0,0,0,0.25); color:inherit; border:1px solid var(--border-color,#444); border-radius:4px; padding:1px 6px; }
.cn-bar button { cursor:pointer; }
.cn-bar button.on { background:#3f6f4f; color:#fff; }
.cn-log { flex:1; overflow-y:auto; padding:4px 6px; margin-top:2px; background:rgba(0,0,0,0.35); border-top:1px solid var(--border-color,#444); white-space:pre-wrap; word-break:break-all; }
.cn-line { display:flex; gap:6px; }
.cn-ts { color:#888; cursor:copy; flex:none; }
.cn-state { color:#888; font-size:10px; margin-left:auto; }
.cn-level-ERROR { color:#ff6b6b; }
.cn-level-WARN { color:#ffb84d; }
.cn-level-STDERR { color:#ffa07a; }
.cn-level-INFO { color:#9fc7ff; }
.cn-level-STDOUT { color:#cfcfcf; }
`;

function injectStyle() {
  if (document.getElementById('cn-style')) return;
  const el = document.createElement('style');
  el.id = 'cn-style';
  el.textContent = STYLE;
  document.head.appendChild(el);
}

function formatTime(ts) {
  try {
    return new Date(ts * 1000).toTimeString().slice(0, 8);
  } catch {
    return '--:--:--';
  }
}

const ANSI_RE = /\x1b\[[0-9;?]*[ -\/]*[@-~]/g;
const cleanText = (t) => (typeof t === 'string' && t.includes('\x1b') ? t.replace(ANSI_RE, '') : t);

function createConsoleView(node) {
  injectStyle();

  const root = document.createElement('div');
  root.className = 'cn-root';

  const bar = document.createElement('div');
  bar.className = 'cn-bar';

  const levelSelect = document.createElement('select');
  for (const key of Object.keys(THRESHOLDS)) {
    const option = document.createElement('option');
    option.value = key;
    option.textContent = key;
    levelSelect.appendChild(option);
  }
  levelSelect.value = 'ALL';

  const filterInput = document.createElement('input');
  filterInput.placeholder = 'filter (regex)';
  filterInput.style.width = '110px';

  const maxInput = document.createElement('input');
  maxInput.type = 'number';
  maxInput.min = '100';
  maxInput.max = '20000';
  maxInput.step = '100';
  maxInput.value = '2000';
  maxInput.style.width = '58px';
  maxInput.title = 'max lines held in the viewer';

  const pauseBtn = document.createElement('button');
  pauseBtn.textContent = 'pause';

  const followBtn = document.createElement('button');
  followBtn.textContent = 'follow';
  followBtn.classList.add('on');

  const clearBtn = document.createElement('button');
  clearBtn.textContent = 'clear';

  const stateLabel = document.createElement('span');
  stateLabel.className = 'cn-state';
  stateLabel.textContent = 'connecting…';

  bar.append(levelSelect, filterInput, maxInput, pauseBtn, followBtn, clearBtn, stateLabel);

  const logEl = document.createElement('div');
  logEl.className = 'cn-log';

  root.append(bar, logEl);

  const view = {
    buffer: [],
    maxLines: 2000,
    paused: false,
    follow: true,
    threshold: THRESHOLDS.ALL,
    regex: null,
    es: null,
    cr: {},
    root,
    logEl,
  };

  const passes = (line) => {
    if ((LEVEL_RANK[line.level] ?? 0) < view.threshold) return false;
    if (view.regex && !view.regex.test(line.text)) return false;
    return true;
  };

  const makeTs = (line) => {
    const ts = document.createElement('span');
    ts.className = 'cn-ts';
    ts.textContent = formatTime(line.ts);
    ts.title = 'click to copy line';
    ts.addEventListener('click', () => {
      const text = `[${formatTime(line.ts)}] ${cleanText(line.text)}`;
      navigator.clipboard?.writeText(text);
    });
    return ts;
  };

  const makeBody = (line) => {
    const body = document.createElement('span');
    body.className = 'cn-level-' + (line.level || 'STDOUT');
    body.textContent = cleanText(line.text);
    return body;
  };

  const makeRow = (line) => {
    const row = document.createElement('div');
    row.className = 'cn-line';
    row.append(makeTs(line), makeBody(line));
    return row;
  };

  const fillRow = (row, line) => {
    row.replaceChildren(makeTs(line), makeBody(line));
  };

  const stick = () => {
    if (view.follow) logEl.scrollTop = logEl.scrollHeight;
  };

  const trim = () => {
    while (view.buffer.length > view.maxLines) view.buffer.shift();
    while (logEl.childElementCount > view.maxLines) logEl.removeChild(logEl.firstChild);
    if (view.cr.row && !view.cr.row.isConnected) view.cr = {};
  };

  const appendLine = (line) => {
    const buf = view.buffer;
    const prev = buf.length ? buf[buf.length - 1] : null;
    const isReplace = !!(prev && prev.cr);
    if (isReplace) buf[buf.length - 1] = line;
    else buf.push(line);
    trim();
    if (view.paused) return;
    const ok = passes(line);
    if (isReplace && view.cr.entry === prev) {
      if (ok) {
        fillRow(view.cr.row, line);
        view.cr = line.cr ? { entry: line, row: view.cr.row } : {};
      } else {
        view.cr.row.remove();
        view.cr = {};
      }
      stick();
      return;
    }
    if (!ok) return;
    const row = makeRow(line);
    logEl.appendChild(row);
    view.cr = line.cr ? { entry: line, row } : {};
    stick();
  };

  const rebuild = () => {
    logEl.replaceChildren();
    view.cr = {};
    const shown = [];
    for (const line of view.buffer) {
      const prev = shown.length ? shown[shown.length - 1] : null;
      if (prev && prev.cr) shown[shown.length - 1] = line;
      else shown.push(line);
    }
    for (const line of shown) {
      if (!passes(line)) continue;
      const row = makeRow(line);
      logEl.appendChild(row);
      view.cr = line.cr ? { entry: line, row } : {};
    }
    stick();
  };

  levelSelect.onchange = () => {
    view.threshold = THRESHOLDS[levelSelect.value] ?? -1;
    rebuild();
  };
  filterInput.oninput = () => {
    const source = filterInput.value.trim();
    try {
      view.regex = source ? new RegExp(source, 'i') : null;
      filterInput.style.borderColor = 'var(--border-color,#444)';
    } catch {
      view.regex = null;
      filterInput.style.borderColor = '#f66';
    }
    rebuild();
  };
  maxInput.onchange = () => {
    const value = parseInt(maxInput.value, 10);
    view.maxLines = Number.isFinite(value) ? Math.max(100, Math.min(20000, value)) : 2000;
    maxInput.value = String(view.maxLines);
    trim();
    rebuild();
  };
  pauseBtn.onclick = () => {
    view.paused = !view.paused;
    pauseBtn.classList.toggle('on', view.paused);
    if (!view.paused) rebuild();
  };
  followBtn.onclick = () => {
    view.follow = !view.follow;
    followBtn.classList.toggle('on', view.follow);
    stick();
  };
  clearBtn.onclick = () => {
    view.buffer = [];
    logEl.replaceChildren();
  };

  const connect = () => {
    try { view.es?.close(); } catch { /* ignore */ }
    const es = new EventSource(api.apiURL(STREAM_PATH));
    view.es = es;
    es.onopen = () => { stateLabel.textContent = 'live'; };
    es.onerror = () => { stateLabel.textContent = 'reconnecting…'; };
    es.addEventListener('backlog', (event) => {
      let lines = [];
      try { lines = JSON.parse(event.data); } catch { return; }
      if (!Array.isArray(lines)) return;
      view.buffer = lines;
      trim();
      rebuild();
    });
    es.addEventListener('line', (event) => {
      let line = null;
      try { line = JSON.parse(event.data); } catch { return; }
      if (line) appendLine(line);
    });
  };

  view.destroy = () => {
    try { view.es?.close(); } catch { /* ignore */ }
  };

  connect();
  return view;
}

app.registerExtension({
  name: 'ComfyUI.ConsoleNode',
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData?.name !== NODE_NAME) return;
    const created = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const result = created?.apply(this, arguments);
      const view = createConsoleView(this);
      this.addDOMWidget('console_view', 'CONSOLE_VIEW', view.root, {
        serialize: false,
        // Growable DOM widget: the layout distributes all remaining node
        // height to it (up to maxHeight). A fixed computeSize would leave a
        // gap at the bottom of the node.
        getMinHeight: () => 160
      });
      const removed = this.onRemoved;
      this.onRemoved = function () {
        view.destroy();
        return removed?.apply(this, arguments);
      };
      this.setSize([Math.max(this.size[0] ?? 0, 460), Math.max(this.size[1] ?? 0, 520)]);
      return result;
    };
  },
});
