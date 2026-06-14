/* AnkiCloud Mini App — вкладки: Создать · Повторять · История.
   Авторизация: каждый запрос несёт заголовок Authorization: tma <initData>,
   сервер проверяет подпись Telegram и достаёт user_id. */

const tg = window.Telegram && window.Telegram.WebApp;
const view = document.getElementById('view');

let CONFIG = null;
let activeTab = 'create';

// состояние вкладки «Создать»
let createState = { text: '', deck: '', subdeck: '', model: null, file: null };
let createView = 'form';      // 'form' | 'editor'
let editorGen = null;
let editorHasSource = false;  // есть ли в createState исходник для «переделать»

const ACCEPT = '.pdf,.txt,.md,.doc,.docx,.ppt,.pptx,.xls,.xlsx,image/*';

// состояние режима повторения
let studySession = null;      // { gen, queue, idx, revealed, done, knew }

// ---------- утилиты ----------

function esc(s) {
  return (s == null ? '' : String(s)).replace(/[&<>"']/g, m =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]));
}

function fmtDate(iso) {
  try { return new Date(iso).toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' }); }
  catch { return ''; }
}

let toastTimer;
function toast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.remove('hidden');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add('hidden'), 2600);
}

function alertMsg(msg) { if (tg && tg.showAlert) tg.showAlert(msg); else toast(msg); }
function confirmDialog(msg) {
  return new Promise(res => {
    if (tg && tg.showConfirm) tg.showConfirm(msg, ok => res(ok));
    else res(window.confirm(msg));
  });
}
function haptic(kind = 'light') { try { tg && tg.HapticFeedback.impactOccurred(kind); } catch {} }
function notify(kind = 'success') { try { tg && tg.HapticFeedback.notificationOccurred(kind); } catch {} }

async function api(path, { method = 'GET', body } = {}) {
  const res = await fetch(path, {
    method,
    headers: {
      'Content-Type': 'application/json',
      'Authorization': 'tma ' + ((tg && tg.initData) || ''),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  let data = null;
  try { data = await res.json(); } catch {}
  if (!res.ok) throw new Error((data && data.error) || ('Ошибка ' + res.status));
  return data;
}

async function apiForm(path, formData) {
  const res = await fetch(path, {
    method: 'POST',
    headers: { 'Authorization': 'tma ' + ((tg && tg.initData) || '') },
    body: formData,
  });
  let data = null;
  try { data = await res.json(); } catch {}
  if (!res.ok) throw new Error((data && data.error) || ('Ошибка ' + res.status));
  return data;
}

// растягивает textarea под содержимое, чтобы был виден весь текст карточки
function autoGrow(el) {
  el.style.height = 'auto';
  el.style.height = (el.scrollHeight + 2) + 'px';
}

function fmtSize(bytes) {
  if (bytes < 1024) return bytes + ' Б';
  if (bytes < 1024 * 1024) return Math.round(bytes / 1024) + ' КБ';
  return (bytes / 1024 / 1024).toFixed(1) + ' МБ';
}

function spinner() { return '<div class="spinner"></div>'; }
function emptyBlock(icon, title, sub) {
  return `<div class="empty"><div class="big">${icon}</div>
    <h2 style="color:var(--text);text-transform:none;letter-spacing:0">${esc(title)}</h2>
    <p class="sub">${esc(sub)}</p></div>`;
}
function errorBlock(msg) {
  return `<div class="empty"><div class="big">⚠️</div><p class="sub">${esc(msg)}</p>
    <button class="btn secondary small" style="margin:0 auto" onclick="location.reload()">Обновить</button></div>`;
}

// ---------- навигация ----------

function setActiveTab(tab) {
  activeTab = tab;
  document.querySelectorAll('.tab').forEach(b =>
    b.classList.toggle('active', b.dataset.tab === tab));
}

function switchTab(tab) {
  setActiveTab(tab);
  if (tab === 'create') renderCreate();
  else if (tab === 'study') renderStudyList();
  else if (tab === 'history') renderHistory();
}

// ---------- вкладка «Создать» ----------

function renderCreate() {
  if (createView === 'editor' && editorGen) renderEditor(editorGen);
  else renderCreateForm();
}

function renderCreateForm() {
  if (createState.model == null) createState.model = (CONFIG && CONFIG.default_model) || null;
  const models = (CONFIG && CONFIG.models) || [];
  const f = createState.file;
  view.innerHTML = `
    <h1>Создать карточки</h1>
    <p class="sub">Вставь текст или прикрепи файл/фото — соберу Anki-карточки. (Голосовые — прямо в чат боту.)</p>
    <div class="section">
      <label>Материал ${f ? '(необязательно — есть файл)' : ''}</label>
      <textarea class="material" id="material" placeholder="Вставь текст лекции, конспект, определения…">${esc(createState.text)}</textarea>
      <label>Файл / фото (необязательно)</label>
      <div style="display:flex;align-items:center;gap:10px;margin-top:4px">
        <label class="btn secondary small" style="margin:0;flex:0 0 auto;cursor:pointer">📎 Прикрепить
          <input type="file" id="file" accept="${ACCEPT}" hidden>
        </label>
        <span id="fname" class="sub" style="margin:0;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${f ? esc(f.name) + ' · ' + fmtSize(f.size) : 'PDF · фото · Word · PPT · Excel'}</span>
        ${f ? '<button class="del" id="fclear" style="flex:0 0 auto">убрать</button>' : ''}
      </div>
      <div class="row">
        <div><label>Колода</label><input type="text" id="deck" placeholder="Онкология" value="${esc(createState.deck)}"></div>
        <div><label>Подколода</label><input type="text" id="subdeck" placeholder="РМЖ" value="${esc(createState.subdeck)}"></div>
      </div>
      ${models.length ? `<label>Модель</label><div class="segmented" id="models">
        ${models.map(m => `<div class="seg ${m.id === createState.model ? 'active' : ''}" data-model="${m.id}">${esc(m.label)}<span class="hint">${esc(m.hint)}</span></div>`).join('')}
      </div>` : `<p class="sub" style="color:var(--destructive);margin-top:10px">⚠️ На сервере не задан LLM-ключ.</p>`}
    </div>
    <button class="btn" id="gen-btn" ${models.length ? '' : 'disabled'}>✨ Сгенерировать</button>
  `;
  const mat = document.getElementById('material');
  mat.addEventListener('input', () => createState.text = mat.value);
  document.getElementById('deck').addEventListener('input', e => createState.deck = e.target.value);
  document.getElementById('subdeck').addEventListener('input', e => createState.subdeck = e.target.value);
  const fileInput = document.getElementById('file');
  fileInput.addEventListener('change', () => { createState.file = fileInput.files[0] || null; haptic(); renderCreateForm(); });
  const fclear = document.getElementById('fclear');
  if (fclear) fclear.addEventListener('click', () => { createState.file = null; renderCreateForm(); });
  document.querySelectorAll('#models .seg').forEach(s => s.addEventListener('click', () => {
    createState.model = s.dataset.model;
    document.querySelectorAll('#models .seg').forEach(x => x.classList.toggle('active', x === s));
    haptic();
  }));
  document.getElementById('gen-btn').addEventListener('click', doGenerate);
}

// Один запрос генерации по текущему createState. feedback — необязательная
// правка от пользователя («переделать с учётом»), добавляется к материалу.
async function requestGeneration(feedback) {
  const text = (createState.text || '').trim();
  const file = createState.file;
  let material = text;
  if (feedback && feedback.trim()) {
    material = (material ? material + '\n\n' : '') +
      '━━━ УКАЗАНИЕ ПО ПЕРЕДЕЛКЕ (выполни эти правки; факты по-прежнему бери только из материала выше): ' +
      feedback.trim();
  }
  if (file) {
    const fd = new FormData();
    fd.append('file', file);
    fd.append('deck', createState.deck || '');
    fd.append('subdeck', createState.subdeck || '');
    fd.append('model', createState.model || '');
    fd.append('text', material);
    return await apiForm('/api/generate_file', fd);
  }
  return await api('/api/generate', {
    method: 'POST',
    body: { text: material, deck: createState.deck, subdeck: createState.subdeck, model: createState.model },
  });
}

async function doGenerate() {
  const text = (createState.text || '').trim();
  const file = createState.file;
  if (!text && !file) { toast('Вставь текст или прикрепи файл'); return; }
  const btn = document.getElementById('gen-btn');
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Генерирую…'; }
  try {
    const gen = await requestGeneration(null);
    notify('success');
    if (gen.note) toast(gen.note);
    editorGen = gen; createView = 'editor'; editorHasSource = true;
    renderEditor(gen);
  } catch (e) {
    notify('error'); alertMsg(e.message);
    if (btn) { btn.disabled = false; btn.textContent = '✨ Сгенерировать'; }
  }
}

// Переделать всю партию по тому же материалу + комментарий пользователя.
async function regenerateWithFeedback() {
  const fb = document.getElementById('feedback');
  const note = (fb && fb.value || '').trim();
  if (!note) { toast('Напиши, что поправить'); return; }
  const btn = document.getElementById('redo-btn');
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Переделываю…'; }
  const prevId = editorGen && editorGen.id;
  try {
    const gen = await requestGeneration(note);
    // старую версию убираем, чтобы в истории не копились дубли
    if (prevId && prevId !== gen.id) {
      try { await api('/api/generations/' + prevId, { method: 'DELETE' }); } catch {}
    }
    notify('success'); toast('Готово — карточки переделаны');
    editorGen = gen; renderEditor(gen);
  } catch (e) {
    notify('error'); alertMsg(e.message);
    if (btn) { btn.disabled = false; btn.textContent = '🔄 Переделать с учётом'; }
  }
}

function renderEditor(gen) {
  createView = 'editor'; editorGen = gen;
  const cards = gen.cards || [];
  view.innerHTML = `
    <h1>${esc(gen.deck)}</h1>
    <p class="sub">${cards.length} карточек · отредактируй или удали лишние, затем добавь все.</p>
    <div id="cards"></div>
    ${editorHasSource ? `<div class="feedback-box">
      <span class="hint">Не то? Напиши, что поправить — переделаю всю партию по тому же материалу.</span>
      <textarea id="feedback" placeholder="Напр.: пропустил раздел про стадии · сделай больше карточек · ответы короче"></textarea>
      <button class="btn secondary small" id="redo-btn" style="margin-top:8px">🔄 Переделать с учётом</button>
    </div>` : ''}
    <div class="actions">
      <button class="btn secondary" id="back-btn">← Ещё</button>
      <button class="btn" id="send-btn">📎 Добавить все (${cards.length})</button>
    </div>
  `;
  const list = document.getElementById('cards');
  if (!cards.length) list.innerHTML = emptyBlock('🃏', 'Карточек нет', 'Все удалены — вернись и создай заново.');
  cards.forEach((c, i) => list.appendChild(cardEditEl(c, i)));
  const redo = document.getElementById('redo-btn');
  if (redo) redo.addEventListener('click', regenerateWithFeedback);
  document.getElementById('back-btn').addEventListener('click', () => {
    createView = 'form'; renderCreateForm();
  });
  document.getElementById('send-btn').addEventListener('click', () => sendApkg(gen));
}

function cardEditEl(c, i) {
  const el = document.createElement('div');
  el.className = 'card-edit';
  el.innerHTML = `
    <div class="num"><span>№${i + 1}</span><button class="del">🗑 удалить</button></div>
    <textarea class="f">${esc(c.front)}</textarea>
    <textarea class="b">${esc(c.back)}</textarea>`;
  const f = el.querySelector('.f'), b = el.querySelector('.b');
  // развернуть оба поля под полный текст (после вставки в DOM)
  [f, b].forEach(t => {
    t.addEventListener('input', () => autoGrow(t));
    requestAnimationFrame(() => autoGrow(t));
  });
  const save = async () => {
    const front = f.value.trim(), back = b.value.trim();
    if (!front || !back || (front === c.front && back === c.back)) return;
    try { await api('/api/cards/' + c.id, { method: 'PATCH', body: { front, back } }); c.front = front; c.back = back; toast('Сохранено'); }
    catch (e) { toast(e.message); }
  };
  f.addEventListener('blur', save);
  b.addEventListener('blur', save);
  el.querySelector('.del').addEventListener('click', async () => {
    if (!await confirmDialog('Удалить карточку?')) return;
    try {
      await api('/api/cards/' + c.id, { method: 'DELETE' });
      haptic();
      editorGen.cards = (editorGen.cards || []).filter(x => x.id !== c.id);
      el.remove();
      const sb = document.getElementById('send-btn');
      if (sb) sb.textContent = `📎 Добавить все (${editorGen.cards.length})`;
    } catch (e) { toast(e.message); }
  });
  return el;
}

async function sendApkg(gen) {
  const btn = document.getElementById('send-btn');
  if (!gen.cards || !gen.cards.length) { toast('Нет карточек'); return; }
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Отправляю…'; }
  try {
    const r = await api('/api/generations/' + gen.id + '/send', { method: 'POST' });
    notify('success');
    alertMsg(`✅ Готово! ${r.count} карточек отправлены файлом .apkg в чат. Открой его в Anki.`);
    createState = { text: '', deck: '', subdeck: '', model: createState.model, file: null };
    createView = 'form';
    switchTab('history');
  } catch (e) {
    notify('error'); alertMsg(e.message);
    if (btn) { btn.disabled = false; btn.textContent = `📎 Добавить все (${(gen.cards || []).length})`; }
  }
}

// ---------- вкладка «История» ----------

async function renderHistory() {
  view.innerHTML = `<h1>История</h1>${spinner()}`;
  let data;
  try { data = await api('/api/generations'); }
  catch (e) { view.innerHTML = `<h1>История</h1>` + errorBlock(e.message); return; }

  const { stats, generations } = data;
  let html = `<h1>История</h1>
    <div class="stats">
      <div class="stat"><div class="num">${stats.generations}</div><div class="lbl">генераций</div></div>
      <div class="stat"><div class="num">${stats.cards}</div><div class="lbl">карточек</div></div>
      <div class="stat"><div class="num">${stats.known}</div><div class="lbl">выучено</div></div>
    </div>`;
  if (!generations.length) {
    view.innerHTML = html + emptyBlock('🗂', 'Пока пусто', 'Создай первую партию во вкладке «Создать».');
    return;
  }
  html += generations.map(genRow).join('');
  view.innerHTML = html;
  wireHistory();
}

function genRow(g) {
  const pct = g.count ? Math.round(g.known / g.count * 100) : 0;
  return `<div class="gen" data-id="${g.id}">
    <div class="title">${esc(g.deck)}</div>
    <div class="meta">${g.count} карточек · выучено ${g.known}/${g.count} · ${fmtDate(g.created_at)}</div>
    <div class="bar"><i style="width:${pct}%"></i></div>
    <div class="gen-actions">
      <button class="btn small secondary" data-act="study">🔁 Повторять</button>
      <button class="btn small secondary" data-act="edit">✏️</button>
      <button class="btn small secondary" data-act="send">📎 .apkg</button>
      <button class="btn small danger" data-act="del">🗑</button>
    </div>
  </div>`;
}

function wireHistory() {
  view.querySelectorAll('.gen').forEach(el => {
    const id = +el.dataset.id;
    el.querySelector('[data-act="study"]').addEventListener('click', () => loadStudy(id));
    el.querySelector('[data-act="edit"]').addEventListener('click', () => openEditor(id));
    el.querySelector('[data-act="del"]').addEventListener('click', async () => {
      if (!await confirmDialog('Удалить эту партию карточек?')) return;
      try { await api('/api/generations/' + id, { method: 'DELETE' }); haptic(); renderHistory(); }
      catch (e) { alertMsg(e.message); }
    });
    const sendBtn = el.querySelector('[data-act="send"]');
    sendBtn.addEventListener('click', async () => {
      sendBtn.disabled = true; sendBtn.textContent = '⏳';
      try { const r = await api('/api/generations/' + id + '/send', { method: 'POST' }); notify('success'); alertMsg(`✅ ${r.count} карточек отправлены в чат файлом .apkg`); }
      catch (e) { alertMsg(e.message); }
      finally { sendBtn.disabled = false; sendBtn.textContent = '📎 .apkg'; }
    });
  });
}

async function openEditor(id) {
  setActiveTab('create');
  view.innerHTML = spinner();
  try { const gen = await api('/api/generations/' + id); editorGen = gen; createView = 'editor'; editorHasSource = false; renderEditor(gen); }
  catch (e) { view.innerHTML = errorBlock(e.message); }
}

// ---------- вкладка «Повторять» ----------

async function renderStudyList() {
  if (studySession) { renderStudyCard(); return; }
  view.innerHTML = `<h1>Повторять</h1>${spinner()}`;
  let data;
  try { data = await api('/api/generations'); }
  catch (e) { view.innerHTML = `<h1>Повторять</h1>` + errorBlock(e.message); return; }

  const gens = data.generations.filter(g => g.count > 0);
  if (!gens.length) {
    view.innerHTML = `<h1>Повторять</h1>` + emptyBlock('🔁', 'Нет карточек', 'Создай карточки во вкладке «Создать», потом повторяй их здесь.');
    return;
  }
  view.innerHTML = `<h1>Повторять</h1><p class="sub">Выбери колоду — карточки листаются как в Anki.</p>` +
    gens.map(g => {
      const left = g.count - g.known;
      const pct = g.count ? Math.round(g.known / g.count * 100) : 0;
      return `<div class="gen" data-id="${g.id}">
        <div class="title">${esc(g.deck)}</div>
        <div class="meta">${g.count} карточек · осталось выучить ${left}</div>
        <div class="bar"><i style="width:${pct}%"></i></div>
        <div class="gen-actions">
          <button class="btn small" data-act="study">▶️ Повторять (${left || g.count})</button>
          ${g.known ? `<button class="btn small secondary" data-act="reset">↺ Заново</button>` : ''}
        </div>
      </div>`;
    }).join('');
  view.querySelectorAll('.gen').forEach(el => {
    const id = +el.dataset.id;
    el.querySelector('[data-act="study"]').addEventListener('click', () => loadStudy(id));
    const r = el.querySelector('[data-act="reset"]');
    if (r) r.addEventListener('click', async () => {
      try { await api('/api/generations/' + id + '/reset', { method: 'POST' }); toast('Прогресс сброшен'); renderStudyList(); }
      catch (e) { alertMsg(e.message); }
    });
  });
}

async function loadStudy(id) {
  setActiveTab('study');
  view.innerHTML = spinner();
  let gen;
  try { gen = await api('/api/generations/' + id); }
  catch (e) { view.innerHTML = errorBlock(e.message); return; }
  let queue = gen.cards.filter(c => !c.known);
  if (!queue.length) queue = gen.cards.slice();
  studySession = { gen, queue, idx: 0, revealed: false, done: 0, knew: 0 };
  renderStudyCard();
}

function renderStudyCard() {
  const s = studySession;
  if (!s) { renderStudyList(); return; }
  if (s.idx >= s.queue.length) { renderStudyDone(); return; }
  const c = s.queue[s.idx];
  const total = s.queue.length;
  const pct = Math.round(s.idx / total * 100);
  view.innerHTML = `
    <div class="study-wrap">
      <div class="progress"><i style="width:${pct}%"></i></div>
      <div class="progress-lbl">${s.idx + 1} / ${total} · ${esc(s.gen.deck)}</div>
      <div class="flashcard" id="card">
        <div class="side-label">Вопрос</div>
        <div>${c.front}</div>
        <div id="answer" class="hidden"></div>
        <div class="tap-hint" id="taphint">нажми, чтобы увидеть ответ</div>
      </div>
      <div class="study-buttons hidden" id="study-btns">
        <button class="btn dont" id="dont">🙁 Не помню</button>
        <button class="btn know" id="know">🙂 Помню</button>
      </div>
      <button class="btn secondary small" id="exit-study" style="margin-top:12px;align-self:center">✕ Выйти</button>
    </div>`;
  document.getElementById('card').addEventListener('click', revealAnswer);
  document.getElementById('exit-study').addEventListener('click', () => { studySession = null; renderStudyList(); });
}

function revealAnswer() {
  const s = studySession;
  if (s.revealed) return;
  s.revealed = true;
  const c = s.queue[s.idx];
  const ans = document.getElementById('answer');
  ans.innerHTML = `<div class="answer"><div class="side-label">Ответ</div>${c.back}</div>`;
  ans.classList.remove('hidden');
  document.getElementById('taphint').classList.add('hidden');
  document.getElementById('study-btns').classList.remove('hidden');
  document.getElementById('dont').addEventListener('click', () => answerCard(false));
  document.getElementById('know').addEventListener('click', () => answerCard(true));
  haptic();
}

async function answerCard(known) {
  const s = studySession;
  const c = s.queue[s.idx];
  notify(known ? 'success' : 'warning');
  try { await api('/api/cards/' + c.id + '/review', { method: 'POST', body: { known } }); } catch {}
  if (known) s.knew++;
  s.done++; s.idx++; s.revealed = false;
  renderStudyCard();
}

function renderStudyDone() {
  const s = studySession;
  view.innerHTML = `
    <div class="empty">
      <div class="big">🎉</div>
      <h2 style="color:var(--text);text-transform:none;letter-spacing:0">Готово!</h2>
      <p class="sub">Повторено ${s.done} · помнишь ${s.knew}/${s.done}</p>
      <div class="actions" style="max-width:320px;margin:18px auto 0">
        <button class="btn secondary" id="again">↺ Ещё раз</button>
        <button class="btn" id="finish">✓ Завершить</button>
      </div>
    </div>`;
  const gid = s.gen.id;
  document.getElementById('again').addEventListener('click', () => loadStudy(gid));
  document.getElementById('finish').addEventListener('click', () => { studySession = null; renderStudyList(); });
}

// ---------- старт ----------

async function boot() {
  if (tg) {
    tg.ready();
    tg.expand();
    try { tg.setHeaderColor('secondary_bg_color'); } catch {}
  }
  document.querySelectorAll('.tab').forEach(b =>
    b.addEventListener('click', () => switchTab(b.dataset.tab)));
  try { CONFIG = await api('/api/config'); }
  catch { CONFIG = { models: [], default_model: null }; }
  switchTab('create');
}

boot();
