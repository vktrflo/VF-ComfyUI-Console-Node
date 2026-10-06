import { app } from '../../scripts/app.js';
import { api } from '../../scripts/api.js';

const NODE_NAME = 'ConsoleLogViewer';
const STREAM_PATH = '/console_node/log/stream';

const LEVEL_RANK = { ERROR: 3, WARN: 2, INFO: 1, STDOUT: 0, STDERR: 0 };
const THRESHOLDS = { ALL: -1, INFO: 1, WARN: 2, ERROR: 3 };

const STYLE = `
.cn-root { display:flex; flex-direction:column; width:100%; height:100%; font-family:ui-monospace, Consolas, monospace; font-size:11px; color:var(--fg-color,#ddd); }
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

  const internalBtn = document.createElement('button');
  internalBtn.textContent = 'int';
  internalBtn.title = 'show internal (console-node) lines';

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

  bar.append(levelSelect, filterInput, maxInput, internalBtn, pauseBtn, followBtn, clearBtn, stateLabel);

  const logEl = document.createElement('div');
  logEl.className = 'cn-log';

  root.append(bar, logEl);

  const view = {
    buffer: [],
    maxLines: 2000,
    paused: false,
    follow: true,
    showInternal: false,
    threshold: THRESHOLDS.ALL,
    regex: null,
    es: null,
    root,
    logEl,
  };

  const passes = (line) => {
    if (!view.showInternal && line.source === 'internal') return false;
    if ((LEVEL_RANK[line.level] ?? 0) < view.threshold) return false;
    if (view.regex && !view.regex.test(line.text)) return false;
    return true;
  };

  const buildLine = (line) => {
    const row = document.createElement('div');
    row.className = 'cn-line';
    const ts = document.createElement('span');
    ts.className = 'cn-ts';
    ts.textContent = formatTime(line.ts);
    ts.title = 'click to copy line';
    ts.addEventListener('click', () => {
      const text = `[${formatTime(line.ts)}] ${line.text}`;
      navigator.clipboard?.writeText(text);
    });
    const body = document.createElement('span');
    body.className = 'cn-level-' + (line.level || 'STDOUT');
    body.textContent = line.text;
    row.append(ts, body);
    return row;
  };

  const stick = () => {
    if (view.follow) logEl.scrollTop = logEl.scrollHeight;
  };

  const trim = () => {
    while (view.buffer.length > view.maxLines) view.buffer.shift();
    while (logEl.childElementCount > view.maxLines) logEl.removeChild(logEl.firstChild);
  };

  const appendLine = (line) => {
    view.buffer.push(line);
    trim();
    if (view.paused || !passes(line)) return;
    logEl.appendChild(buildLine(line));
    stick();
  };

  const rebuild = () => {
    logEl.replaceChildren();
    for (const line of view.buffer) {
      if (passes(line)) logEl.appendChild(buildLine(line));
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
  internalBtn.onclick = () => {
    view.showInternal = !view.showInternal;
    internalBtn.classList.toggle('on', view.showInternal);
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
      const widget = this.addDOMWidget('console_view', 'CONSOLE_VIEW', view.root, { serialize: false });
      widget.computeSize = () => [this.size[0] - 16, Math.max(160, this.size[1] - 70)];
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
