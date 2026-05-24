/* ============================================================
   Prompt Hacker - Lehrer-Dashboard
   ============================================================ */

const $ = (sel) => document.querySelector(sel);

let overview = { students: [], pending_requests: [], tokens: { today: 0, limit: 0 } };
let activeStudentDetailId = null;
let detailRefreshInFlight = false;

function headers() {
  return {
    'Content-Type': 'application/json',
    'X-Teacher-Action-Token': window.TEACHER_ACTION_TOKEN,
  };
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { ...headers(), ...(opts.headers || {}) },
    ...opts,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: 'Fehler' }));
    throw new Error(err.error || `HTTP ${res.status}`);
  }
  return res.json();
}

async function loadOverview() {
  overview = await api('/api/teacher/overview');
  renderOverview();
  await refreshActiveStudentDetails();
}

function renderOverview() {
  const students = overview.students || [];
  const pending = overview.pending_requests || [];
  const pendingAttemptRounds = overview.pending_attempt_rounds || [];
  $('#kpi-students').textContent = students.length;
  $('#kpi-online').textContent = students.filter((s) => s.online).length;
  $('#kpi-pending').textContent = pending.length + pendingAttemptRounds.length;
  $('#kpi-tokens').textContent = overview.tokens.today;

  renderClassGameControl();
  renderRequests(pending, pendingAttemptRounds);
  renderStudents(students);
}

function renderClassGameControl() {
  const button = $('#class-game-toggle');
  if (!button) return;
  const unlocked = overview.class_game_unlocked !== false;
  button.textContent = unlocked ? 'Spiel global sperren' : 'Spiel global freigeben';
  button.dataset.unlocked = unlocked ? '0' : '1';
  button.classList.toggle('btn-danger', unlocked);
  button.classList.toggle('btn-secondary', !unlocked);
}

function renderRequests(items, attemptRounds = []) {
  const box = $('#pending-requests');
  if (!items.length && !attemptRounds.length) {
    box.innerHTML = '<p class="muted">Keine offenen Anfragen.</p>';
    return;
  }

  const kiHtml = items.map((req) => `
    <div class="request-card" data-request="${req.id}">
      <div class="request-meta">
        <strong>${escapeHtml(req.pseudonym)}</strong>
        <span>${escapeHtml(req.klasse)} · ${escapeHtml(req.created_at)}</span>
      </div>
      <p>${escapeHtml(req.question)}</p>
      <div class="dashboard-actions">
        <button class="btn-primary" data-action="approve" data-id="${req.id}">Freigeben</button>
        <button class="btn-ghost" data-action="deny" data-id="${req.id}">Ablehnen</button>
      </div>
    </div>
  `).join('');

  const roundHtml = attemptRounds.map((req) => `
    <div class="request-card" data-attempt-request="${req.id}">
      <div class="request-meta">
        <strong>${escapeHtml(req.pseudonym)}</strong>
        <span>${escapeHtml(req.klasse)} · Level ${req.level_id} · ${escapeHtml(req.created_at)}</span>
      </div>
      <p>Neue Versuchsrunde angefragt: ${req.amount || 5} weitere Versuche.</p>
      <div class="dashboard-actions">
        <button class="btn-primary" data-action="approveAttemptRound" data-id="${req.id}">5 Versuche freigeben</button>
        <button class="btn-ghost" data-action="denyAttemptRound" data-id="${req.id}">Ablehnen</button>
      </div>
    </div>
  `).join('');

  box.innerHTML = `
    ${attemptRounds.length ? `<h3 class="request-group-title">Versuchsrunden</h3>${roundHtml}` : ''}
    ${items.length ? `<h3 class="request-group-title">KI-Tutor</h3>${kiHtml}` : ''}
  `;
}

function renderStudents(students) {
  const box = $('#student-table');
  if (!students.length) {
    box.innerHTML = '<p class="muted">Noch keine Schueler:innen angemeldet.</p>';
    return;
  }

  box.innerHTML = `
    <div class="student-row student-head">
      <span>Name</span>
      <span>Spiel</span>
      <span>Arbeitsblatt 2</span>
      <span>Aktionen</span>
    </div>
    ${students.map((s) => `
      <div class="student-row" data-student="${s.id}">
        <span>
          <strong>${escapeHtml(s.pseudonym)}</strong>
          <small>${escapeHtml(s.klasse)} · ${s.online ? 'online' : 'offline'}</small>
        </span>
        <span>
          <strong>${overview.class_game_unlocked === false ? 'global gesperrt' : (s.personal_game_unlocked ? 'frei' : 'gesperrt')}</strong>
          <small>${s.solved}/3 geloest · ${s.attempts} Versuche${s.pending_attempt_rounds ? ` · ${s.pending_attempt_rounds} Anfrage offen` : ''}</small>
        </span>
        <span>
          <strong>${s.worksheet_unlocked ? 'frei' : 'gesperrt'}</strong>
          <small>${s.pending} KI-Tutor offen</small>
        </span>
        <span class="dashboard-actions">
          <button class="btn-outline" data-action="details" data-id="${s.id}">Details</button>
          <button class="btn-ghost" data-action="toggleGame" data-id="${s.id}" data-unlocked="${s.personal_game_unlocked ? '0' : '1'}">
            ${s.personal_game_unlocked ? 'Spiel sperren' : 'Spiel freigeben'}
          </button>
          <button class="btn-primary" data-action="toggleWorksheet" data-id="${s.id}" data-unlocked="${s.worksheet_unlocked ? '0' : '1'}">
            ${s.worksheet_unlocked ? 'AB2 sperren' : 'AB2 freigeben'}
          </button>
          <button class="btn-secondary" data-action="grant" data-id="${s.id}">+5 Versuche</button>
        </span>
      </div>
    `).join('')}
  `;
}

async function approveRequest(id) {
  await api(`/api/teacher/request/${id}/approve`, { method: 'POST', body: '{}' });
  await loadOverview();
}

async function denyRequest(id) {
  await api(`/api/teacher/request/${id}/deny`, {
    method: 'POST',
    body: JSON.stringify({ reason: 'Bitte versuche zuerst den Infotext oder frage im Unterricht nach.' }),
  });
  await loadOverview();
}

async function approveAttemptRound(id) {
  await api(`/api/teacher/attempt_round/${id}/approve`, { method: 'POST', body: '{}' });
  await loadOverview();
}

async function denyAttemptRound(id) {
  await api(`/api/teacher/attempt_round/${id}/deny`, {
    method: 'POST',
    body: JSON.stringify({ reason: 'Bitte besprich deine Strategie kurz mit der Lehrkraft.' }),
  });
  await loadOverview();
}

async function toggleClassGame(unlocked) {
  await api('/api/teacher/class/toggle_game', {
    method: 'POST',
    body: JSON.stringify({ unlocked }),
  });
  await loadOverview();
}

async function toggleGame(id, unlocked) {
  await api(`/api/teacher/student/${id}/toggle_game`, {
    method: 'POST',
    body: JSON.stringify({ unlocked }),
  });
  await loadOverview();
}

async function toggleWorksheet(id, unlocked) {
  await api(`/api/teacher/student/${id}/toggle_worksheet`, {
    method: 'POST',
    body: JSON.stringify({ unlocked }),
  });
  await loadOverview();
}

async function grantAttempts(id) {
  const level = prompt('Fuer welches Level sollen +5 Versuche freigegeben werden? (1, 2 oder 3)', '1');
  const levelId = Number(level);
  if (![1, 2, 3].includes(levelId)) return;
  await api(`/api/teacher/student/${id}/grant_attempts`, {
    method: 'POST',
    body: JSON.stringify({ level_id: levelId, amount: 5 }),
  });
  await loadOverview();
}

async function deleteSessionData() {
  const confirmed = confirm(
    'Wirklich alle Sessiondaten löschen? Dadurch werden Schüler:innen, Spielstände, Arbeitsblatt-Antworten, KI-Anfragen und Token-Zähler entfernt.'
  );
  if (!confirmed) return;
  activeStudentDetailId = null;
  await api('/api/teacher/session_data', { method: 'DELETE' });
  closeSessionReport();
  await loadOverview();
}

function triggerSessionReportLoad() {
  $('#session-report-input')?.click();
}

function closeSessionReport() {
  const viewer = $('#session-report-viewer');
  if (viewer) viewer.hidden = true;
  activeStudentDetailId = null;
  document.body.classList.remove('report-print-mode');
}

function printSessionReport() {
  window.print();
}

async function showStudentDetails(id, opts = {}) {
  activeStudentDetailId = Number(id);
  const data = await api(`/api/teacher/student/${id}/details`);
  const student = data.student;
  renderSessionReport({
    format: 'prompt_hacker_session_report',
    exported_at: 'Wird automatisch aktualisiert, solange diese Detailansicht geöffnet ist.',
    mode: 'live-detail',
    title: `Detailansicht: ${student.pseudonym}`,
    students: [student],
  }, opts);
}

async function refreshActiveStudentDetails() {
  if (!activeStudentDetailId || detailRefreshInFlight) return;
  detailRefreshInFlight = true;
  try {
    await showStudentDetails(activeStudentDetailId, { scroll: false });
  } catch (err) {
    console.warn('Detailansicht konnte nicht aktualisiert werden:', err);
  } finally {
    detailRefreshInFlight = false;
  }
}

function formatRole(role) {
  if (role === 'user') return 'Schüler:in';
  if (role === 'model' || role === 'ai') return 'KI';
  return role || 'Eintrag';
}

function renderReportLevel(level) {
  const history = level.history || [];
  return `
    <details class="report-subsection" ${history.length || level.solved ? 'open' : ''}>
      <summary>
        <strong>Level ${level.level_id}: ${escapeHtml(level.name)}</strong>
        <span>${level.solved ? 'geknackt' : 'offen'} · ${level.attempts}/${level.max_attempts} Versuche</span>
      </summary>
      ${history.length
        ? `<div class="report-dialogue">${history.map((turn) => `
            <div class="report-turn ${turn.role === 'user' ? 'is-user' : 'is-ai'}">
              <strong>${escapeHtml(formatRole(turn.role))}</strong>
              <p>${escapeHtml(turn.content)}</p>
            </div>
          `).join('')}</div>`
        : '<p class="muted">Kein Dialog gespeichert.</p>'}
    </details>
  `;
}

function renderReportAnswers(answers) {
  if (!answers.length) return '<p class="muted">Keine Arbeitsblatt-Antworten gespeichert.</p>';
  return answers.map((answer) => `
    <article class="report-answer">
      <strong>${escapeHtml(answer.task_id)} · Niveau ${escapeHtml((answer.niveau || '').toUpperCase())}</strong>
      <p>${escapeHtml(answer.answer || '')}</p>
    </article>
  `).join('');
}

function renderReportRequests(requests) {
  if (!requests.length) return '<p class="muted">Keine KI-Tutor-Anfragen gespeichert.</p>';
  return requests.map((req) => `
    <article class="report-answer">
      <strong>${escapeHtml(req.status)} · ${escapeHtml(req.created_at)}</strong>
      <p><b>Frage:</b> ${escapeHtml(req.question || '')}</p>
      ${req.response ? `<p><b>Antwort:</b> ${escapeHtml(req.response)}</p>` : ''}
    </article>
  `).join('');
}

function renderReportAttemptRounds(requests) {
  if (!requests.length) return '<p class="muted">Keine Versuchsrunden-Anfragen gespeichert.</p>';
  return requests.map((req) => `
    <article class="report-answer">
      <strong>${escapeHtml(req.status)} · Level ${escapeHtml(req.level_id)} · ${escapeHtml(req.created_at)}</strong>
      <p>${escapeHtml(req.amount || 5)} weitere Versuche${req.reason ? ` · ${escapeHtml(req.reason)}` : ''}</p>
    </article>
  `).join('');
}

function renderSessionReport(report, opts = {}) {
  if (!report || report.format !== 'prompt_hacker_session_report') {
    throw new Error('Diese Datei ist kein Prompt-Hacker-Sessionbericht.');
  }

  const students = report.students || [];
  $('#session-report-eyebrow').textContent = report.mode === 'live-detail' ? 'Live-Detail' : 'Geladener Bericht';
  $('#session-report-title').textContent = report.title || `Sessionbericht (${students.length} Schüler:innen)`;
  $('#session-report-meta').textContent = report.exported_at || 'Exportdatum unbekannt';
  $('#session-report-content').innerHTML = students.length ? students.map((student) => `
    <article class="report-student">
      <header>
        <div>
          <h3>${escapeHtml(student.pseudonym)}</h3>
          <span>${escapeHtml(student.klasse)} · zuletzt ${escapeHtml(student.last_seen || 'unbekannt')}</span>
        </div>
        <strong>${(student.levels || []).filter((lvl) => lvl.solved).length}/${(student.levels || []).length} Level</strong>
      </header>

      <section>
        <h4>Spielverlauf</h4>
        ${(student.levels || []).length
          ? student.levels.map(renderReportLevel).join('')
          : '<p class="muted">Keine Leveldaten gespeichert.</p>'}
      </section>

      <section>
        <h4>Arbeitsblatt</h4>
        ${renderReportAnswers(student.worksheet_answers || [])}
      </section>

      <section>
        <h4>KI-Tutor</h4>
        ${renderReportRequests(student.ki_requests || [])}
      </section>

      <section>
        <h4>Versuchsrunden</h4>
        ${renderReportAttemptRounds(student.attempt_round_requests || [])}
      </section>
    </article>
  `).join('') : '<p class="muted">Dieser Bericht enthält keine Schülerdaten.</p>';

  $('#session-report-viewer').hidden = false;
  document.body.classList.add('report-print-mode');
  if (opts.scroll !== false) {
    $('#session-report-viewer').scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
}

async function loadSessionReportFile(file) {
  if (!file) return;
  activeStudentDetailId = null;
  const text = await file.text();
  renderSessionReport(JSON.parse(text));
}

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text ?? '';
  return div.innerHTML;
}

document.addEventListener('DOMContentLoaded', async () => {
  const socket = typeof io !== 'undefined' ? io() : null;
  if (socket) {
    socket.emit('teacher_join');
    socket.on('new_ki_request', loadOverview);
    socket.on('request_decided', loadOverview);
    socket.on('new_attempt_round_request', loadOverview);
    socket.on('attempt_request_decided', loadOverview);
    socket.on('student_joined', loadOverview);
    socket.on('student_updated', loadOverview);
    socket.on('student_offline', loadOverview);
    socket.on('session_data_deleted', () => {
      closeSessionReport();
      loadOverview();
    });
    socket.on('token_update', (data) => {
      overview.tokens = data;
      $('#kpi-tokens').textContent = data.today;
    });
  }

  document.body.addEventListener('click', async (event) => {
    const button = event.target.closest('button[data-action]');
    if (!button) return;
    button.disabled = true;
    try {
      const id = button.dataset.id;
      if (button.dataset.action === 'approve') await approveRequest(id);
      if (button.dataset.action === 'deny') await denyRequest(id);
      if (button.dataset.action === 'approveAttemptRound') await approveAttemptRound(id);
      if (button.dataset.action === 'denyAttemptRound') await denyAttemptRound(id);
      if (button.dataset.action === 'toggleClassGame') await toggleClassGame(button.dataset.unlocked === '1');
      if (button.dataset.action === 'toggleGame') await toggleGame(id, button.dataset.unlocked === '1');
      if (button.dataset.action === 'toggleWorksheet') await toggleWorksheet(id, button.dataset.unlocked === '1');
      if (button.dataset.action === 'grant') await grantAttempts(id);
      if (button.dataset.action === 'details') await showStudentDetails(id);
      if (button.dataset.action === 'deleteSessionData') await deleteSessionData();
      if (button.dataset.action === 'loadSessionReport') triggerSessionReportLoad();
      if (button.dataset.action === 'closeSessionReport') closeSessionReport();
      if (button.dataset.action === 'printSessionReport') printSessionReport();
    } catch (err) {
      alert(err.message);
    } finally {
      button.disabled = false;
    }
  });

  $('#session-report-input')?.addEventListener('change', async (event) => {
    try {
      await loadSessionReportFile(event.target.files?.[0]);
    } catch (err) {
      alert('Bericht konnte nicht geladen werden: ' + err.message);
    } finally {
      event.target.value = '';
    }
  });

  await loadOverview();
  setInterval(loadOverview, 5000);
});
