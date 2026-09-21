/* ==========================================================================
   畅Agent Web 界面交互
   数据来源：SSE（Server-Sent Events），事件协议见 docs/WEB.md
   ========================================================================== */

const $ = (sel) => document.querySelector(sel);

const state = {
  es: null,            // EventSource 实例
  running: false,
  startedAt: 0,
  toolCount: 0,
  changeCount: 0,
  currentGroup: null,  // 当前运行的分组容器
  cards: new Map(),    // 工具调用 id -> 卡片元素
  autoScroll: true,
};

/* -------------------------------------------------------------------------- */
/* 初始化                                                                      */
/* -------------------------------------------------------------------------- */

async function init() {
  renderEmpty();
  bindEvents();

  try {
    const res = await fetch('/api/state');
    if (!res.ok) throw new Error('HTTP ' + res.status);
    applyState(await res.json());
  } catch (err) {
    $('#file-tree').innerHTML = '<div class="side-empty">无法连接后端服务</div>';
    setRunStatus('后端未就绪');
  }
}

function applyState(data) {
  $('#workspace-path').textContent = data.workspace || '—';
  $('#model-name').textContent = data.model || '未配置';

  const badge = $('#mode-badge');
  const live = data.mode === 'live';
  badge.textContent = live ? '真实模式' : '演示模式';
  badge.classList.toggle('is-live', live);
  badge.title = live
    ? '已接入真实模型，事件来自 Agent 内核'
    : '演示模式：播放一次完整的 Agent 工作流，无需配置 API Key';

  renderFileTree(data.files || []);
  renderQuickTasks(data.demo_task);
}

/* -------------------------------------------------------------------------- */
/* 侧栏渲染                                                                    */
/* -------------------------------------------------------------------------- */

function renderFileTree(files) {
  const box = $('#file-tree');
  box.innerHTML = '';

  if (!files.length) {
    box.innerHTML = '<div class="side-empty">工作区为空或不存在</div>';
    $('#file-count').textContent = '0';
    return;
  }

  for (const item of files) {
    const row = document.createElement('div');
    row.className = 'file-item' + (item.type === 'dir' ? ' is-dir' : '');
    row.style.paddingLeft = (6 + item.depth * 14) + 'px';
    row.title = item.path;

    const icon = document.createElement('span');
    icon.className = 'fi-icon';
    icon.textContent = item.type === 'dir' ? '▸' : '·';

    const name = document.createElement('span');
    name.className = 'fi-name';
    name.textContent = item.path.split('/').pop();

    const size = document.createElement('span');
    size.className = 'fi-size';
    size.textContent = item.type === 'file' ? formatSize(item.size) : '';

    row.append(icon, name, size);
    box.appendChild(row);
  }

  $('#file-count').textContent = String(files.length);
}

function renderQuickTasks(demoTask) {
  const tasks = [
    demoTask || '在 hello.py 中新增 greet(name) 函数',
    '把 hello.py 的问候语改成中文',
    '列出工作区所有文件，并统计代码行数',
  ];

  const box = $('#quick-list');
  box.innerHTML = '';

  for (const task of tasks) {
    const btn = document.createElement('button');
    btn.className = 'quick-btn';
    btn.type = 'button';
    btn.textContent = task;
    btn.addEventListener('click', () => {
      $('#task-input').value = task;
      autoGrow();
      if (!state.running) run(task);
    });
    box.appendChild(btn);
  }
}

function formatSize(bytes) {
  if (bytes == null) return '';
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / 1024 / 1024).toFixed(1) + ' MB';
}

/* -------------------------------------------------------------------------- */
/* 空状态                                                                      */
/* -------------------------------------------------------------------------- */

function streamInner() {
  let inner = $('#stream').querySelector('.stream-inner');
  if (!inner) {
    inner = document.createElement('div');
    inner.className = 'stream-inner';
    $('#stream').appendChild(inner);
  }
  return inner;
}

function renderEmpty() {
  const box = streamInner();
  box.innerHTML = '';

  const el = document.createElement('div');
  el.className = 'empty-state rise';
  el.innerHTML = `
    <div class="empty-mark">畅</div>
    <h1>让你的 Agent 动手改代码</h1>
    <p>用一句自然语言描述需求，它自己读文件、改文件、汇报改动。</p>
    <p class="empty-note">当前为演示模式：完整播放一次真实工作流，无需配置 API Key</p>`;
  box.appendChild(el);
}

/* -------------------------------------------------------------------------- */
/* 运行                                                                        */
/* -------------------------------------------------------------------------- */

function run(task) {
  if (state.running) return;

  resetRun();
  pushUserMessage(task);
  setRunningUi(true);

  state.running = true;
  state.startedAt = Date.now();

  const es = new EventSource('/api/run?task=' + encodeURIComponent(task));
  state.es = es;

  es.onmessage = (e) => {
    let event;
    try { event = JSON.parse(e.data); } catch { return; }
    handleEvent(event);
  };

  es.onerror = () => {
    // 流被服务端正常关闭时也会触发，此时已由 done 事件收尾，忽略即可
    if (state.running) finishRun(false, '连接中断');
  };
}

function stop() {
  if (!state.running) return;
  if (state.es) { state.es.close(); state.es = null; }
  finishRun(false, '已手动停止');
}

function handleEvent(ev) {
  switch (ev.type) {
    case 'agent_start': onAgentStart(ev); break;
    case 'thinking':    onThinking(ev);   break;
    case 'tool_call':   onToolCall(ev);   break;
    case 'tool_result': onToolResult(ev); break;
    case 'file_change': onFileChange(ev); break;
    case 'answer':      onAnswer(ev);     break;
    case 'error':       onError(ev);      break;
    case 'done':        onDone(ev);       break;
  }
}

function ensureGroup() {
  if (state.currentGroup) return state.currentGroup;

  const group = document.createElement('section');
  group.className = 'run-group rise';
  group.innerHTML = `
    <div class="run-head is-running">
      <span class="dot"></span>
      <span class="run-label">Agent 工作中</span>
    </div>
    <div class="run-body"></div>`;

  streamInner().appendChild(group);
  state.currentGroup = group;
  return group;
}

function runBody() {
  return ensureGroup().querySelector('.run-body');
}

/* ---- 各事件渲染 ---- */

function onAgentStart() {
  ensureGroup();
  if (!$('#stat-state').textContent.includes('运行')) {
    $('#stat-state').textContent = '运行中';
  }
}

function onThinking(ev) {
  const el = document.createElement('div');
  el.className = 'think-block rise';

  const tag = document.createElement('span');
  tag.className = 'think-tag';
  tag.textContent = '思考';

  const text = document.createElement('p');
  text.textContent = ev.text || '';

  el.append(tag, text);
  runBody().appendChild(el);
  scrollToBottom();
}

function onToolCall(ev) {
  const card = document.createElement('article');
  card.className = 'tool-card is-running rise';
  card.innerHTML = `
    <header class="tool-head">
      <span class="tool-dot"></span>
      <span class="tool-name"></span>
      <span class="tool-args"></span>
      <span class="tool-time"></span>
      <span class="tool-caret">▾</span>
    </header>
    <div class="tool-body">
      <div class="tool-section">参数</div>
      <pre class="code args"></pre>
      <div class="tool-section">结果</div>
      <pre class="code out">等待返回…</pre>
    </div>`;

  card.querySelector('.tool-name').textContent = ev.name;
  card.querySelector('.tool-args').textContent = summarizeArgs(ev.name, ev.args);
  card.querySelector('.args').textContent = JSON.stringify(ev.args || {}, null, 2);
  card.querySelector('.tool-head').addEventListener('click', () => {
    card.classList.toggle('is-collapsed');
  });

  runBody().appendChild(card);
  state.cards.set(ev.id, card);
  state.toolCount += 1;
  $('#stat-tools').textContent = String(state.toolCount);
  scrollToBottom();
}

function onToolResult(ev) {
  const card = state.cards.get(ev.id);
  if (!card) return;

  card.classList.remove('is-running');
  card.classList.add(ev.ok ? 'is-ok' : 'is-error');
  card.querySelector('.tool-time').textContent = ev.elapsed_ms != null ? ev.elapsed_ms + ' ms' : '';
  card.querySelector('.out').textContent = ev.content || '';

  // 内容不长时默认展开，太长时折叠起来保持界面清爽
  const long = (ev.content || '').split('\n').length > 28;
  if (long) card.classList.add('is-collapsed');

  scrollToBottom();
}

function onFileChange(ev) {
  const group = runBody();

  const card = document.createElement('article');
  card.className = 'change-card rise';

  const head = document.createElement('div');
  head.className = 'change-head';

  const path = document.createElement('span');
  path.className = 'ch-path';
  path.textContent = ev.path;

  const stat = document.createElement('span');
  stat.className = 'ch-stat';
  stat.innerHTML = `<span class="add">+${ev.added}</span> <span class="del">−${ev.removed}</span>`;

  head.append(path, stat);
  card.append(head, buildDiff(ev.diff || []));
  group.appendChild(card);

  addChangeMini(ev);
  scrollToBottom();
}

function onAnswer(ev) {
  const el = document.createElement('div');
  el.className = 'answer-block rise';
  el.innerHTML = `<div class="answer-tag">执行完成</div><p class="answer-text"></p>`;
  el.querySelector('.answer-text').textContent = ev.text || '';
  runBody().appendChild(el);
  scrollToBottom();
}

function onError(ev) {
  ensureGroup();
  const el = document.createElement('div');
  el.className = 'error-block rise';
  el.textContent = ev.text || '发生未知错误';
  runBody().appendChild(el);
  scrollToBottom();
}

function onDone(ev) {
  if (state.es) { state.es.close(); state.es = null; }
  finishRun(ev.ok !== false, null, ev);
}

/* -------------------------------------------------------------------------- */
/* 右侧变更面板                                                                 */
/* -------------------------------------------------------------------------- */

function addChangeMini(ev) {
  const list = $('#change-list');
  if (state.changeCount === 0) list.innerHTML = '';

  const box = document.createElement('div');
  box.className = 'change-mini rise';

  const head = document.createElement('div');
  head.className = 'cm-head';

  const name = document.createElement('span');
  name.textContent = ev.path;

  const stat = document.createElement('span');
  stat.className = 'cm-stat';
  stat.innerHTML = `<span class="add">+${ev.added}</span> <span class="del">−${ev.removed}</span>`;

  head.append(name, stat);
  box.append(head, buildDiff(ev.diff || []));
  list.appendChild(box);

  state.changeCount += 1;
  $('#change-count').textContent = String(state.changeCount);
}

function buildDiff(diff) {
  const box = document.createElement('div');
  box.className = 'diff';

  for (const line of diff) {
    const row = document.createElement('div');
    row.className = 'diff-line ' +
      (line.t === '+' ? 'is-add' : line.t === '-' ? 'is-del' : 'is-ctx');

    const sign = document.createElement('span');
    sign.className = 'sign';
    sign.textContent = line.t === '+' ? '+' : line.t === '-' ? '-' : ' ';

    const text = document.createElement('span');
    text.className = 'txt';
    text.textContent = line.text === '' ? ' ' : line.text;

    row.append(sign, text);
    box.appendChild(row);
  }
  return box;
}

/* -------------------------------------------------------------------------- */
/* 状态与工具函数                                                               */
/* -------------------------------------------------------------------------- */

function finishRun(ok, note, doneEvent) {
  state.running = false;
  setRunningUi(false);

  const head = state.currentGroup && state.currentGroup.querySelector('.run-head');
  if (head) {
    head.classList.remove('is-running');
    head.querySelector('.dot').classList.add(ok ? 'is-done' : 'is-fail');
    head.querySelector('.run-label').textContent = ok
      ? 'Agent 完成'
      : ('Agent 已停止' + (note ? '：' + note : ''));
  }

  const stateEl = $('#stat-state');
  stateEl.textContent = ok ? '完成' : (note || '未完成');
  stateEl.className = ok ? 'is-ok' : 'is-err';

  if (doneEvent && doneEvent.steps != null) {
    $('#stat-steps').textContent = String(doneEvent.steps);
  }
  if (state.startedAt) {
    $('#stat-elapsed').textContent = ((Date.now() - state.startedAt) / 1000).toFixed(1) + ' s';
  }
  setRunStatus('');
}

function resetRun() {
  state.cards.clear();
  state.currentGroup = null;
  state.toolCount = 0;
  state.changeCount = 0;
  state.autoScroll = true;

  streamInner().innerHTML = '';
  $('#change-list').innerHTML = '<div class="side-empty">本次运行还没有改动</div>';
  $('#change-count').textContent = '0';
  $('#stat-state').textContent = '运行中';
  $('#stat-state').className = '';
  $('#stat-steps').textContent = '—';
  $('#stat-tools').textContent = '—';
  $('#stat-elapsed').textContent = '—';
}

function pushUserMessage(task) {
  const wrap = document.createElement('div');
  wrap.className = 'msg-user rise';

  const bubble = document.createElement('div');
  bubble.className = 'bubble';
  bubble.textContent = task;

  wrap.appendChild(bubble);
  streamInner().appendChild(wrap);
  scrollToBottom();
}

function setRunningUi(running) {
  const btn = $('#run-btn');
  btn.classList.toggle('is-stop', running);
  btn.querySelector('.btn-label').textContent = running ? '停止' : '运行';
  btn.title = running ? '停止本次运行' : '运行（Ctrl + Enter）';
  setRunStatus(running ? '运行中…' : '');
}

function setRunStatus(text) {
  $('#run-status').textContent = text;
}

function summarizeArgs(name, args) {
  if (!args) return '';

  if (name === 'grep_search') {
    return `/${args.pattern || ''}/` + (args.include ? `  ${args.include}` : '');
  }
  if (name === 'glob_search') {
    return args.pattern || '';
  }
  if (args.path) {
    let text = args.path;
    if (name === 'edit_file') text += args.replace_all ? '  (全部替换)' : '  (单处替换)';
    if (name === 'read_file' && args.offset && args.offset > 1) text += `  从第 ${args.offset} 行`;
    return text;
  }
  return Object.keys(args).join(', ');
}

function scrollToBottom() {
  const el = $('#stream');
  if (state.autoScroll) el.scrollTop = el.scrollHeight;
}

function autoGrow() {
  const input = $('#task-input');
  input.style.height = 'auto';
  input.style.height = Math.min(input.scrollHeight, 160) + 'px';
}

/* -------------------------------------------------------------------------- */
/* 事件绑定                                                                    */
/* -------------------------------------------------------------------------- */

function bindEvents() {
  const input = $('#task-input');
  const btn = $('#run-btn');

  btn.addEventListener('click', () => {
    if (state.running) { stop(); return; }
    const task = input.value.trim();
    if (task) run(task);
  });

  input.addEventListener('input', autoGrow);

  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      if (state.running) return;
      const task = input.value.trim();
      if (task) run(task);
    }
  });

  $('#stream').addEventListener('scroll', () => {
    const el = $('#stream');
    state.autoScroll = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  });

  $('#reset-btn').addEventListener('click', () => {
    if (state.running) stop();
    resetRun();
    renderEmpty();
  });
}

document.addEventListener('DOMContentLoaded', init);
