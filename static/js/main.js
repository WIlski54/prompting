/* ============================================================
   Prompt Hacker - Schüleroberfläche
   ============================================================ */

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

const levelImages = {
  1: '/static/img/bot1.png',
  2: '/static/img/bot2.png',
  3: '/static/img/bot3.png',
};

const state = {
  levels: [],
  currentLevelId: null,
  worksheet: {},
  gameUnlocked: true,
  worksheetUnlocked: false,
  pendingTutorRequest: null,
  socket: null,
  classGameUnlocked: true,
  personalGameUnlocked: true,
};

function showScreen(name) {
  if ((name === 'worksheet' || name === 'summary') && !state.worksheetUnlocked) {
    showWorksheetLockedMessage();
    name = 'intro';
  }
  $$('.screen').forEach((s) => s.classList.remove('active'));
  const target = $(`#screen-${name}`);
  if (!target) return;
  target.classList.add('active');
  $$('.top-tab').forEach((tab) => {
    tab.classList.toggle('active', tab.dataset.target === name);
  });
  if (name === 'summary') renderSummary();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function showWorksheetLockedMessage() {
  const note = $('#worksheet-lock-note');
  if (note) {
    note.classList.add('pulse-lock');
    window.setTimeout(() => note.classList.remove('pulse-lock'), 900);
  }
  alert('Arbeitsblatt 2 und Auswertung werden nach dem Spiel von der Lehrkraft freigegeben.');
}

function ensurePolicyAccepted() {
  return true;
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) },
    ...opts,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: 'Fehler' }));
    throw new Error(err.error || `HTTP ${res.status}`);
  }
  return res.json();
}

function setupSocket() {
  if (typeof io === 'undefined') return;
  state.socket = io();
  state.socket.on('connect', () => {});
  state.socket.on('ki_response', (data) => {
    if (state.pendingTutorRequest && data.request_id !== state.pendingTutorRequest) return;
    state.pendingTutorRequest = null;
    appendTutorMessage('ai', data.response);
    setTutorWaiting(false, 'Freigegeben und beantwortet.');
  });
  state.socket.on('ki_denied', (data) => {
    if (state.pendingTutorRequest && data.request_id !== state.pendingTutorRequest) return;
    state.pendingTutorRequest = null;
    appendTutorMessage('ai', data.reason || 'Die Lehrkraft hat die Anfrage abgelehnt.');
    setTutorWaiting(false, 'Anfrage abgelehnt.');
  });
  state.socket.on('game_lock_update', async (data) => {
    await loadLevels();
    if (state.currentLevelId) {
      const lvl = state.levels.find((item) => item.id === state.currentLevelId);
      if (lvl) {
        updateAttemptInfo(lvl);
        updateHintUI(lvl.attempts, lvl.hints_used, lvl.solved);
        renderReflectionUI(lvl);
      }
    }
    if (!state.gameUnlocked && $('#screen-game').classList.contains('active')) {
      appendMsg($('#chat-log'), 'ai', data.message || 'Die Lehrkraft hat das Spiel gerade gesperrt.', ['error']);
    }
  });
  state.socket.on('worksheet_lock_update', async (data) => {
    state.worksheetUnlocked = Boolean(data.worksheet_unlocked);
    updateWorksheetLockUI();
    if (state.worksheetUnlocked) {
      alert('Das Arbeitsblatt und die Auswertung sind jetzt freigegeben.');
      window.location.reload();
      return;
    }
    state.worksheet = {};
    alert('Das Arbeitsblatt und die Auswertung wurden wieder gesperrt.');
    window.location.reload();
  });
  state.socket.on('attempts_update', async () => {
    await loadLevels();
    if (state.currentLevelId) {
      const lvl = state.levels.find((item) => item.id === state.currentLevelId);
      if (lvl) {
        updateAttemptInfo(lvl);
        updateHintUI(lvl.attempts, lvl.hints_used, lvl.solved);
        renderReflectionUI(lvl);
      }
    }
  });
  state.socket.on('attempt_request_decided', async (data) => {
    await loadLevels();
    if (state.currentLevelId) {
      const lvl = state.levels.find((item) => item.id === state.currentLevelId);
      if (lvl) {
        updateAttemptInfo(lvl);
        updateHintUI(lvl.attempts, lvl.hints_used, lvl.solved);
        renderReflectionUI(lvl);
      }
    }
    if (data.status === 'approved') {
      appendMsg($('#chat-log'), 'ai', 'Die Lehrkraft hat dir eine neue Versuchsrunde freigegeben.', ['intro']);
    } else if (data.status === 'denied') {
      appendMsg($('#chat-log'), 'ai', data.reason || 'Die Lehrkraft hat die neue Versuchsrunde noch nicht freigegeben.', ['error']);
    }
  });
  state.socket.on('session_reset', (data) => {
    alert(data.message || 'Die Unterrichtsdaten wurden gelöscht. Bitte melde dich neu an.');
    window.location.href = '/logout';
  });
}

async function loadLevels() {
  const data = await api('/api/levels');
  state.levels = data.levels;
  state.gameUnlocked = Boolean(data.game_unlocked);
  state.classGameUnlocked = data.class_game_unlocked !== false;
  state.personalGameUnlocked = data.personal_game_unlocked !== false;
  state.worksheetUnlocked = Boolean(data.worksheet_unlocked);
  renderLevelGrid();
  updateWorksheetLockUI();
}

function updateWorksheetLockUI() {
  const locked = !state.worksheetUnlocked;
  $$('.top-tab').forEach((tab) => {
    if (tab.dataset.target === 'worksheet' || tab.dataset.target === 'summary') {
      tab.classList.toggle('locked', locked);
      tab.setAttribute('aria-disabled', locked ? 'true' : 'false');
      tab.title = locked ? 'Wird durch die Lehrkraft freigegeben' : '';
    }
  });

  const openButton = $('#btn-open-worksheet');
  if (openButton) {
    openButton.textContent = locked ? 'Arbeitsblatt 2 noch gesperrt' : 'Zu Arbeitsblatt 2';
    openButton.classList.toggle('is-locked', locked);
  }

  const modalButton = $('#btn-modal-worksheet');
  if (modalButton) {
    modalButton.textContent = locked ? 'Wartet auf Freigabe' : 'Zu Arbeitsblatt 2';
    modalButton.classList.toggle('is-locked', locked);
  }

  const note = $('#worksheet-lock-note');
  if (note) {
    note.textContent = locked
      ? 'Arbeitsblatt 2, Auswertung und KI-Tutor werden nach der Spielphase von der Lehrkraft freigegeben.'
      : 'Arbeitsblatt 2, Auswertung und KI-Tutor sind freigegeben.';
    note.classList.toggle('unlocked', !locked);
  }
}

function renderLevelGrid() {
  const grid = $('#level-grid');
  grid.innerHTML = '';
  state.levels.forEach((lvl) => {
    const card = document.createElement('button');
    card.type = 'button';
    card.className = 'level-card' + (lvl.solved ? ' solved' : '');
    card.disabled = !state.gameUnlocked;
    card.innerHTML = `
      <img class="level-portrait" src="${levelImages[lvl.id]}" alt="${escapeHtml(lvl.name)}">
      <div class="level-card-body">
        <h3>Level ${lvl.id}: ${escapeHtml(lvl.name)}</h3>
        <p class="level-subtitle">${escapeHtml(lvl.title)}</p>
        <p class="level-desc">${escapeHtml(lvl.description)}</p>
        <div class="level-footer">
          <span class="difficulty-badge" data-level="${escapeHtml(lvl.difficulty)}">${escapeHtml(lvl.difficulty)}</span>
          ${lvl.solved
            ? '<span class="solved-badge">✓ Geknackt</span>'
            : lvl.attempt_request_pending
              ? '<span>Anfrage offen</span>'
            : `<span>${lvl.remaining_attempts}/${lvl.max_attempts} Versuche frei</span>`}
        </div>
      </div>
    `;
    card.addEventListener('click', () => enterLevel(lvl.id));
    grid.appendChild(card);
  });

  if (!state.gameUnlocked) {
    const note = document.createElement('div');
    note.className = 'card lock-note';
    note.textContent = state.classGameUnlocked
      ? 'Das Spiel ist aktuell für dich durch die Lehrkraft gesperrt.'
      : 'Das Spiel ist aktuell für die ganze Klasse gesperrt.';
    grid.appendChild(note);
  }
}

function enterLevel(levelId) {
  if (!state.gameUnlocked) {
    alert('Das Spiel ist gerade nicht freigegeben.');
    return;
  }
  const lvl = state.levels.find((l) => l.id === levelId);
  if (!lvl) return;
  state.currentLevelId = levelId;

  const guardianImage = $('#guardian-image');
  guardianImage.src = levelImages[lvl.id];
  guardianImage.alt = lvl.name;
  $('#guardian-name').textContent = `${lvl.name} - ${lvl.title}`;
  $('#guardian-title').textContent = `Level ${lvl.id}`;
  $('#guardian-description').textContent = lvl.description;
  const diffBadge = $('#guardian-difficulty');
  diffBadge.textContent = lvl.difficulty;
  diffBadge.dataset.level = lvl.difficulty;

  renderChat(lvl);
  updateAttemptInfo(lvl);
  updateHintUI(lvl.attempts, lvl.hints_used, lvl.solved);
  renderReflectionUI(lvl);
  $('#hint-display').innerHTML = '';
  $('#chat-input').value = '';
  $('#chat-input').focus();
  showScreen('game');
}

function updateAttemptInfo(lvl) {
  $('#attempts-count').textContent = `${lvl.attempts}/${lvl.max_attempts}`;
  setSolvedStatus(lvl.solved);
  const sendBtn = $('#btn-send');
  const input = $('#chat-input');
  const requestBtn = $('#btn-request-round');
  const reflection = $('#success-reflection');
  const limitReached = !lvl.solved && lvl.remaining_attempts <= 0;
  if (lvl.solved) {
    sendBtn.disabled = true;
    input.disabled = true;
    input.placeholder = 'Level geschafft. Notiere unten deine Erfolgsstrategie.';
    if (requestBtn) requestBtn.hidden = true;
    if (reflection) reflection.hidden = false;
    return;
  }
  if (reflection) reflection.hidden = true;
  const blocked = !state.gameUnlocked || limitReached;
  sendBtn.disabled = blocked;
  input.disabled = blocked;
  if (requestBtn) {
    requestBtn.hidden = !limitReached || !state.gameUnlocked;
    requestBtn.disabled = Boolean(lvl.attempt_request_pending);
    requestBtn.textContent = lvl.attempt_request_pending
      ? 'Neue Versuchsrunde angefragt'
      : 'Neue Versuchsrunde anfragen';
  }
  if (!state.gameUnlocked) {
    input.placeholder = state.classGameUnlocked
      ? 'Das Spiel ist für dich gerade gesperrt.'
      : 'Das Spiel ist für die ganze Klasse gerade gesperrt.';
  } else if (limitReached) {
    input.placeholder = lvl.attempt_request_pending
      ? 'Anfrage läuft. Warte auf die Freigabe der Lehrkraft.'
      : 'Versuchslimit erreicht. Fordere eine neue Runde mit 5 Versuchen an.';
  } else {
    input.placeholder = 'Schreibe deine Nachricht an den Wächter...';
  }
}

function renderChat(lvl) {
  const log = $('#chat-log');
  log.innerHTML = '';
  appendMsg(log, 'ai', lvl.intro, ['intro']);
  lvl.history.forEach((turn) => {
    appendMsg(log, turn.role === 'user' ? 'user' : 'ai', turn.content);
  });
  log.scrollTop = log.scrollHeight;
}

function appendMsg(log, kind, text, extraClasses = []) {
  const div = document.createElement('div');
  div.className = `chat-msg ${kind} ${extraClasses.join(' ')}`;
  div.textContent = text;
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
  return div;
}

function setSolvedStatus(solved) {
  const el = $('#solved-status');
  if (solved) {
    el.textContent = '✓ geknackt';
    el.classList.add('solved');
  } else {
    el.textContent = 'offen';
    el.classList.remove('solved');
  }
}

function updateHintUI(attempts, hintsUsed, solved = false) {
  const btn = $('#btn-hint');
  const status = $('#hint-status');
  const maxHints = 3;

  if (solved) {
    btn.hidden = true;
    btn.disabled = true;
    btn.textContent = 'Level geschafft';
    status.textContent = 'Kein Tipp mehr nötig - du hast dieses Level geknackt.';
    return;
  }
  btn.hidden = false;

  if (hintsUsed >= maxHints) {
    btn.disabled = true;
    btn.textContent = 'Alle Tipps gezeigt';
    status.textContent = `Du hast alle ${maxHints} Tipps gesehen.`;
    return;
  }

  const nextThreshold = (hintsUsed + 1) * 5;
  const available = attempts >= nextThreshold;
  btn.disabled = !available;
  btn.textContent = available ? `Tipp ${hintsUsed + 1} anzeigen` : 'Tipp anzeigen';
  status.textContent = available
    ? 'Tipp verfügbar - klicke auf den Button.'
    : `Nach ${nextThreshold} Versuchen kannst du einen Tipp anfordern. (${attempts}/${nextThreshold})`;
}

function renderReflectionUI(lvl) {
  const card = $('#success-reflection');
  if (!card) return;
  card.hidden = !lvl.solved;
  const input = $('#reflection-input');
  if (input && document.activeElement !== input) {
    input.value = lvl.strategy_reflection || '';
  }
  const status = $('#reflection-status');
  if (status) {
    status.textContent = lvl.strategy_reflection ? 'Gespeichert.' : '';
  }
}

async function sendMessage(text) {
  const log = $('#chat-log');
  const input = $('#chat-input');
  const sendBtn = $('#btn-send');

  appendMsg(log, 'user', text);
  input.value = '';
  input.style.height = 'auto';

  const thinking = appendMsg(log, 'ai', 'Der Wächter denkt nach', ['thinking']);
  sendBtn.disabled = true;

  try {
    const data = await api(
      `/api/level/${state.currentLevelId}/attempt`,
      { method: 'POST', body: JSON.stringify({ message: text }) }
    );

    thinking.classList.remove('thinking');
    thinking.textContent = data.ai_response;
    setSolvedStatus(data.solved);
    updateHintUI(data.attempts, data.hints_used, data.solved);

    const lvl = state.levels.find((l) => l.id === state.currentLevelId);
    if (lvl) {
      lvl.attempts = data.attempts;
      lvl.remaining_attempts = data.remaining_attempts;
      lvl.max_attempts = data.max_attempts;
      lvl.solved = data.solved;
      lvl.hints_used = data.hints_used;
      lvl.history = lvl.history || [];
      lvl.history.push({ role: 'user', content: text });
      lvl.history.push({ role: 'model', content: data.ai_response });
      updateAttemptInfo(lvl);
      renderReflectionUI(lvl);
    }

    if (data.just_solved) {
      setTimeout(() => showSuccessModal(data.lesson), 600);
      setTimeout(() => $('#reflection-input')?.focus(), 650);
    }
  } catch (err) {
    thinking.classList.remove('thinking');
    thinking.textContent = `Fehler: ${err.message}`;
    thinking.classList.add('error');
  } finally {
    const lvl = state.levels.find((l) => l.id === state.currentLevelId);
    if (!lvl || (state.gameUnlocked && lvl.remaining_attempts > 0)) sendBtn.disabled = false;
    input.focus();
  }
}

async function requestAttemptRound() {
  if (!state.currentLevelId) return;
  const button = $('#btn-request-round');
  if (button) button.disabled = true;
  try {
    const data = await api(
      `/api/level/${state.currentLevelId}/attempt_round_request`,
      { method: 'POST', body: '{}' }
    );
    await loadLevels();
    const lvl = state.levels.find((l) => l.id === state.currentLevelId);
    if (lvl) {
      lvl.attempt_request_pending = true;
      updateAttemptInfo(lvl);
    }
    appendMsg(
      $('#chat-log'),
      'ai',
      data.already_pending
        ? 'Deine Anfrage für eine neue Versuchsrunde liegt der Lehrkraft bereits vor.'
        : 'Deine Anfrage für eine neue Versuchsrunde wurde an die Lehrkraft geschickt.',
      ['intro']
    );
  } catch (err) {
    alert('Fehler: ' + err.message);
    if (button) button.disabled = false;
  }
}

async function saveReflection() {
  if (!state.currentLevelId) return;
  const input = $('#reflection-input');
  const status = $('#reflection-status');
  const button = $('#btn-save-reflection');
  const reflection = (input?.value || '').trim();
  if (button) button.disabled = true;
  if (status) status.textContent = 'Speichert...';
  try {
    await api(
      `/api/level/${state.currentLevelId}/reflection`,
      { method: 'POST', body: JSON.stringify({ reflection }) }
    );
    const lvl = state.levels.find((l) => l.id === state.currentLevelId);
    if (lvl) lvl.strategy_reflection = reflection;
    if (status) status.textContent = 'Gespeichert.';
  } catch (err) {
    if (status) status.textContent = err.message;
  } finally {
    if (button) button.disabled = false;
  }
}

async function fetchHint() {
  try {
    const data = await api(
      `/api/level/${state.currentLevelId}/hint`,
      { method: 'POST' }
    );
    const display = $('#hint-display');
    const item = document.createElement('div');
    item.className = 'hint-item';
    item.innerHTML = `<span class="hint-num">Tipp ${data.hint_number}:</span> ${escapeHtml(data.hint)}`;
    display.appendChild(item);

    const lvl = state.levels.find((l) => l.id === state.currentLevelId);
    if (lvl) lvl.hints_used = data.hint_number;
    updateHintUI(parseInt(String($('#attempts-count').textContent).split('/')[0], 10), data.hint_number);
  } catch (err) {
    alert('Fehler: ' + err.message);
  }
}

function showSuccessModal(lesson) {
  $('#lesson-technique').textContent = lesson.technique;
  $('#lesson-explanation').textContent = lesson.explanation;
  $('#lesson-real-world-text').textContent = lesson.real_world;
  $('#success-modal').hidden = false;
}

function closeSuccessModal() {
  $('#success-modal').hidden = true;
}

async function loadWorksheetAnswers() {
  if (!state.worksheetUnlocked) {
    state.worksheet = {};
    updateWorksheetProgress();
    return;
  }
  const data = await api('/api/worksheet/answers');
  if (data.locked) {
    state.worksheetUnlocked = false;
    state.worksheet = {};
    updateWorksheetLockUI();
    hydrateWorksheet();
    return;
  }
  state.worksheet = data.answers || {};
  hydrateWorksheet();
}

function setWorksheetPanel(panel) {
  $$('.worksheet-tab').forEach((tab) => {
    tab.classList.toggle('active', tab.dataset.panel === panel);
  });
  $$('.worksheet-panel').forEach((item) => {
    item.classList.toggle('active', item.id === `panel-${panel}`);
  });
}

function setTaskNiveau(card, level) {
  card.querySelectorAll('.niveau-switch button').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.level === level);
  });
  card.querySelectorAll('.niveau-content').forEach((content) => {
    content.classList.toggle('active', content.dataset.level === level);
  });
  const taskId = card.dataset.task;
  state.worksheet[taskId] = { ...(state.worksheet[taskId] || {}), niveau: level };
}

function hydrateWorksheet() {
  $$('.task-card').forEach((card) => {
    const taskId = card.dataset.task;
    const taskState = state.worksheet[taskId] || {};
    const level = taskState.niveau || taskState.level || 'a';
    setTaskNiveau(card, level);
    const answer = card.querySelector('.task-answer');
    answer.value = taskState.answer || '';
    card.classList.toggle('saved', Boolean(taskState.answer));
  });
  updateWorksheetProgress();
}

async function saveTask(card) {
  if (!state.worksheetUnlocked) {
    showWorksheetLockedMessage();
    return;
  }
  const taskId = card.dataset.task;
  const niveau = card.querySelector('.niveau-switch button.active')?.dataset.level || 'a';
  const answer = card.querySelector('.task-answer').value.trim();
  await api('/api/worksheet/answer', {
    method: 'POST',
    body: JSON.stringify({ task_id: taskId, niveau, answer }),
  });
  state.worksheet[taskId] = { task_id: taskId, niveau, answer };
  card.classList.toggle('saved', Boolean(answer));
  updateWorksheetProgress();
}

function updateWorksheetProgress() {
  const progress = $('#worksheet-progress');
  if (!progress) return;
  const count = ['task1', 'task2', 'task3', 'task4', 'task5']
    .filter((taskId) => state.worksheet[taskId]?.answer?.trim()).length;
  progress.textContent = String(count);
}

function appendTutorMessage(kind, text) {
  appendMsg($('#tutor-log'), kind, text);
}

function setTutorWaiting(waiting, status) {
  $('#btn-tutor-request').disabled = waiting;
  $('#tutor-input').disabled = waiting;
  $('#tutor-status').textContent = status;
}

async function requestTutorHelp(question) {
  if (!state.worksheetUnlocked) {
    showWorksheetLockedMessage();
    return;
  }
  appendTutorMessage('user', question);
  setTutorWaiting(true, 'Warte auf Freigabe durch die Lehrkraft...');
  try {
    const data = await api('/api/ki/request', {
      method: 'POST',
      body: JSON.stringify({ question }),
    });
    state.pendingTutorRequest = data.request_id;
    $('#tutor-input').value = question;
  } catch (err) {
    setTutorWaiting(false, `Fehler: ${err.message}`);
  }
}

function renderSummary() {
  const wrapper = $('#summary-levels');
  const solved = state.levels.filter((lvl) => lvl.solved).length;

  wrapper.innerHTML = `
    <div class="summary-tile">
      <strong>${solved}/${state.levels.length}</strong>
      <span>Wächter geknackt</span>
    </div>
    <div class="summary-tile">
      <strong>${state.worksheetUnlocked ? 'frei' : 'gesperrt'}</strong>
      <span>Arbeitsblatt 2</span>
    </div>
    <div class="summary-tile">
      <strong>${solved >= 1 ? 'bereit' : 'offen'}</strong>
      <span>für die Auswertung im Unterricht</span>
    </div>
  `;
}

function escapeHtml(s) {
  const div = document.createElement('div');
  div.textContent = s;
  return div.innerHTML;
}

document.addEventListener('DOMContentLoaded', async () => {
  setupSocket();
  await loadLevels();
  await loadWorksheetAnswers();

  $$('.top-tab').forEach((tab) => {
    tab.addEventListener('click', () => showScreen(tab.dataset.target));
  });

  $('#btn-start').addEventListener('click', () => showScreen('basics'));
  $('#btn-basics-to-game')?.addEventListener('click', () => showScreen('select'));
  $('#btn-open-worksheet').addEventListener('click', () => showScreen('worksheet'));

  $('#btn-back').addEventListener('click', async () => {
    state.currentLevelId = null;
    await loadLevels();
    showScreen('select');
  });

  $('#chat-form').addEventListener('submit', (e) => {
    e.preventDefault();
    const text = $('#chat-input').value.trim();
    if (text) sendMessage(text);
  });

  $('#chat-input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      $('#chat-form').requestSubmit();
    }
  });

  $('#chat-input').addEventListener('input', (e) => {
    e.target.style.height = 'auto';
    e.target.style.height = Math.min(e.target.scrollHeight, 200) + 'px';
  });

  $('#btn-hint').addEventListener('click', fetchHint);
  $('#btn-request-round').addEventListener('click', requestAttemptRound);
  $('#btn-save-reflection').addEventListener('click', saveReflection);
  $('#btn-reset-all').addEventListener('click', () => alert('Zurücksetzen läuft jetzt über das Lehrer-Dashboard.'));
  $('#btn-reset-level').addEventListener('click', () => alert('Bei Bedarf kann die Lehrkraft im Dashboard Zusatzversuche geben.'));
  $('#btn-modal-close').addEventListener('click', closeSuccessModal);
  $('#btn-modal-worksheet').addEventListener('click', () => {
    closeSuccessModal();
    showScreen('worksheet');
  });

  $('#success-modal').addEventListener('click', (e) => {
    if (e.target === $('#success-modal')) closeSuccessModal();
  });

  $$('.worksheet-tab').forEach((tab) => {
    tab.addEventListener('click', () => setWorksheetPanel(tab.dataset.panel));
  });

  $$('.task-card').forEach((card) => {
    card.querySelectorAll('.niveau-switch button').forEach((btn) => {
      btn.addEventListener('click', () => setTaskNiveau(card, btn.dataset.level));
    });
    card.querySelector('.save-task').addEventListener('click', async () => {
      try {
        await saveTask(card);
      } catch (err) {
        alert('Fehler beim Speichern: ' + err.message);
      }
    });
  });

  const tutorForm = $('#tutor-form');
  if (tutorForm) {
    tutorForm.addEventListener('submit', (e) => {
      e.preventDefault();
      const question = $('#tutor-input').value.trim();
      if (question) requestTutorHelp(question);
    });
  }

  $('#btn-summary-worksheet')?.addEventListener('click', () => showScreen('worksheet'));
  $('#btn-summary-game')?.addEventListener('click', () => showScreen('select'));
});
