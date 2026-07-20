const metricGrid = document.querySelector('#metric-grid');
const trackBars = document.querySelector('#track-bars');
const slotBars = document.querySelector('#slot-bars');
const dailyBars = document.querySelector('#daily-bars');
const recentSessions = document.querySelector('#recent-sessions');
const individualFlags = document.querySelector('#individual-flags');
const individualCount = document.querySelector('#individual-count');
const tabButtons = [...document.querySelectorAll('[data-tab]')];
const overviewPanel = document.querySelector('#overview-panel');
const triagePanel = document.querySelector('#triage-panel');
const filterButtons = [...document.querySelectorAll('[data-filter]')];
const triageTitle = document.querySelector('#triage-title');
const triageDescription = document.querySelector('#triage-description');
const triageSearch = document.querySelector('#triage-search');
const triageSort = document.querySelector('#triage-sort');
const toggleDetailsButton = document.querySelector('#toggle-details');
const exportCsvButton = document.querySelector('#export-csv');

let currentData = null;
let activeTab = 'overview';
let activeFilter = 'all';
let searchQuery = '';
let sortMode = 'priority';
let detailsExpanded = false;
let visibleRecords = [];
const statusEl = document.querySelector('#stats-status');

function number(value) {
  return new Intl.NumberFormat('ko-KR').format(value ?? 0);
}

function percent(value) {
  return `${Math.round((value ?? 0) * 100)}%`;
}

function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

function appendMetric(label, value, hint) {
  const card = document.createElement('article');
  card.className = 'metric-card';
  const strong = document.createElement('strong');
  strong.textContent = value;
  const span = document.createElement('span');
  span.textContent = label;
  const small = document.createElement('small');
  small.textContent = hint;
  card.append(strong, span, small);
  metricGrid.appendChild(card);
}

function appendBar(container, label, value, max, suffix = '명') {
  const row = document.createElement('div');
  row.className = 'bar-row';
  const top = document.createElement('div');
  top.className = 'bar-top';
  const name = document.createElement('span');
  name.textContent = label;
  const count = document.createElement('b');
  count.textContent = `${number(value)}${suffix}`;
  top.append(name, count);
  const track = document.createElement('div');
  track.className = 'bar-track';
  const fill = document.createElement('div');
  fill.className = 'bar-fill';
  fill.style.width = `${max ? Math.max(4, Math.round((value / max) * 100)) : 0}%`;
  track.appendChild(fill);
  row.append(top, track);
  container.appendChild(row);
}

function severityLabel(value) {
  return { high: '긴급', medium: '주의', low: '관찰' }[value] || value || '관찰';
}

function appendIndividualFlag(record) {
  const card = document.createElement('article');
  card.className = `individual-item severity-${record.severity || 'low'}`;

  const head = document.createElement('div');
  head.className = 'individual-head';

  const title = document.createElement('div');
  const strong = document.createElement('strong');
  strong.textContent = `${record.participant_id} · ${record.track}`;
  const meta = document.createElement('span');
  meta.textContent = `${record.session_id} · 사용자 ${record.user_turns}턴`;
  title.append(strong, meta);

  const severity = document.createElement('span');
  severity.className = `severity-pill severity-${record.severity || 'low'}`;
  severity.textContent = severityLabel(record.severity);
  head.append(title, severity);

  const disclosure = document.createElement('details');
  disclosure.className = 'individual-disclosure';
  disclosure.open = detailsExpanded;

  const summary = document.createElement('summary');
  summary.textContent = `특이사항 ${(record.flags || []).length}개 · 세부 정보 보기`;
  disclosure.appendChild(summary);

  const flags = document.createElement('ul');
  flags.className = 'flag-list';
  (record.flags || []).forEach((flag) => {
    const li = document.createElement('li');
    const label = document.createElement('b');
    label.textContent = flag.label;
    const detail = document.createElement('span');
    detail.textContent = flag.detail;
    li.append(label, detail);
    flags.appendChild(li);
  });

  const details = document.createElement('dl');
  details.className = 'individual-details';
  const detailRows = [
    ['호소', record.chief_complaint || '미확인'],
    ['지지', record.support || '미확인'],
    ['기대', record.expectation || '미확인'],
    ['미확인', record.missing && record.missing.length ? record.missing.join(', ') : '없음'],
  ];
  if (record.track === '중독') {
    detailRows.splice(1, 0,
      ['중독 유형', record.addiction_type || '미확인'],
      ['안내 긴급도', record.addiction_severity || '미확인'],
      ['전문기관 연결', record.addiction_referral || '미확인'],
    );
  }
  detailRows.forEach(([label, value]) => {
    const dt = document.createElement('dt');
    dt.textContent = label;
    const dd = document.createElement('dd');
    dd.textContent = value;
    details.append(dt, dd);
  });

  disclosure.append(flags, details);
  card.append(head, disclosure);
  individualFlags.appendChild(card);
}

function isCrisisRecord(record) {
  const labels = (record.flags || []).map((flag) => flag.label).join(' ');
  return record.track === '위기' || record.severity === 'high' || labels.includes('위기');
}

function matchesFilter(record, filter) {
  const labels = (record.flags || []).map((flag) => flag.label).join(' ');
  if (filter === 'all') return true;
  if (filter === 'crisis') return isCrisisRecord(record);
  if (filter === 'addiction') return record.track === '중독' || labels.includes('중독');
  if (filter === 'high') return record.severity === 'high';
  if (filter === 'medium') return record.severity === 'medium';
  if (filter === 'support') return labels.includes('지지체계') || labels.includes('지지');
  if (filter === 'missing') return labels.includes('미확인') || (record.missing || []).length > 0;
  if (filter === 'early') return labels.includes('이탈');
  return true;
}

function recordSearchText(record) {
  return [
    record.participant_id,
    record.session_id,
    record.track,
    record.chief_complaint,
    record.support,
    record.expectation,
    ...(record.missing || []),
    ...(record.flags || []).flatMap((flag) => [flag.label, flag.detail]),
  ].join(' ').toLocaleLowerCase('ko-KR');
}

function sortRecords(records) {
  const severity = { high: 0, medium: 1, low: 2 };
  return [...records].sort((a, b) => {
    if (sortMode === 'participant') {
      return a.participant_id.localeCompare(b.participant_id, 'ko-KR');
    }
    if (sortMode === 'track') {
      return a.track.localeCompare(b.track, 'ko-KR') ||
        a.participant_id.localeCompare(b.participant_id, 'ko-KR');
    }
    return (severity[a.severity] ?? 9) - (severity[b.severity] ?? 9) ||
      a.participant_id.localeCompare(b.participant_id, 'ko-KR');
  });
}

function csvCell(value) {
  return `"${String(value ?? '').replaceAll('"', '""')}"`;
}

function exportVisibleCsv() {
  if (!visibleRecords.length) return;
  const header = [
    '개인번호', '세션', '트랙', '심각도', '특이사항', '호소',
    '중독 유형', '중독 안내 긴급도', '전문기관 연결', '지지', '기대', '미확인',
  ];
  const rows = visibleRecords.map((record) => [
    record.participant_id,
    record.session_id,
    record.track,
    severityLabel(record.severity),
    (record.flags || []).map((flag) => flag.label).join(' | '),
    record.chief_complaint,
    record.addiction_type,
    record.addiction_severity,
    record.addiction_referral,
    record.support,
    record.expectation,
    (record.missing || []).join(' | '),
  ]);
  const csv = [header, ...rows].map((row) => row.map(csvCell).join(',')).join('\n');
  const blob = new Blob([`\uFEFF${csv}`], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = `intake-triage-${activeTab}-${new Date().toISOString().slice(0, 10)}.csv`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function setFilter(filter) {
  activeFilter = filter;
  filterButtons.forEach((button) => {
    const active = button.dataset.filter === filter;
    button.classList.toggle('active', active);
    button.setAttribute('aria-pressed', active ? 'true' : 'false');
  });
  renderIndividualFlags();
}

function setTab(tab) {
  activeTab = tab;
  tabButtons.forEach((button) => {
    const active = button.dataset.tab === tab;
    button.classList.toggle('active', active);
    button.setAttribute('aria-selected', active ? 'true' : 'false');
  });

  const overview = tab === 'overview';
  overviewPanel.hidden = !overview;
  triagePanel.hidden = overview;

  window.history.replaceState(null, '', `#${tab}`);
  const visiblePanel = overview ? overviewPanel : triagePanel;
  visiblePanel.classList.remove('panel-enter');
  void visiblePanel.offsetWidth;
  visiblePanel.classList.add('panel-enter');

  if (tab === 'crisis') {
    triageTitle.textContent = '위기 우선 확인';
    triageDescription.textContent = '위기 트랙·긴급 red flag 세션을 우선 모아 보여줍니다.';
    if (activeFilter !== 'crisis' && activeFilter !== 'high') {
      setFilter('crisis');
      return;
    }
  } else if (tab === 'management') {
    triageTitle.textContent = '관리 대상';
    triageDescription.textContent = '지지체계 취약, 미확인, 조기 이탈 등 후속 관리가 필요한 세션입니다.';
    if (activeFilter === 'crisis') {
      setFilter('all');
      return;
    }
  }

  renderIndividualFlags();
}

function renderIndividualFlags() {
  if (!currentData) return;
  clear(individualFlags);

  let flagged = currentData.individual_flags || [];
  if (activeTab === 'crisis') {
    flagged = flagged.filter(isCrisisRecord);
  }
  flagged = flagged.filter((record) => matchesFilter(record, activeFilter));
  if (searchQuery) {
    flagged = flagged.filter((record) => recordSearchText(record).includes(searchQuery));
  }
  flagged = sortRecords(flagged);
  visibleRecords = flagged;
  if (exportCsvButton) exportCsvButton.disabled = !flagged.length;

  individualCount.textContent = `${number(flagged.length)}건`;
  if (!flagged.length) {
    const empty = document.createElement('p');
    empty.className = 'empty-note';
    empty.textContent = '현재 필터에서는 특이 사항이 감지된 세션이 없습니다.';
    individualFlags.appendChild(empty);
  } else {
    flagged.forEach(appendIndividualFlag);
  }
}

function renderStats(data) {
  clear(metricGrid);
  clear(trackBars);
  clear(slotBars);
  clear(dailyBars);
  clear(recentSessions);
  clear(individualFlags);
  currentData = data;

  const totals = data.totals || {};
  appendMetric('개인번호', number(totals.participants), 'participants 테이블');
  appendMetric('세션', number(totals.conversations), 'conversations 테이블');
  appendMetric('대화 턴', number(totals.turns), 'user + assistant + summary');
  appendMetric('위기 세션', number(totals.red_flag_sessions), 'red flag 요약 포함');
  appendMetric('요약 생성', number(totals.summaries), 'intake_summary 적재');
  appendMetric('특이 세션', number(totals.notable_sessions), '사람 검토 triage');
  appendMetric('평균 사용자 턴', number(totals.avg_user_turns_per_conversation), '세션당 입력 수');

  const trackMax = Math.max(1, ...data.track_counts.map((item) => item.count));
  data.track_counts.forEach((item) => appendBar(trackBars, item.track, item.count, trackMax));
  if (!data.track_counts.length) appendBar(trackBars, '아직 적재 없음', 0, 1);

  const slotMax = Math.max(1, ...data.slot_completion.map((item) => item.completed));
  data.slot_completion.slice(0, 9).forEach((item) => {
    appendBar(slotBars, `${item.label} (${percent(item.rate)})`, item.completed, slotMax);
  });

  const dailyMax = Math.max(1, ...data.daily_counts.map((item) => item.conversations));
  data.daily_counts.forEach((item) => {
    appendBar(dailyBars, item.date, item.conversations, dailyMax, '세션');
  });

  data.recent_sessions.forEach((session) => {
    const row = document.createElement('tr');
    [
      session.date,
      session.session_id,
      session.participant_id,
      session.track,
      session.red_flags && session.red_flags.length ? '있음' : '없음',
    ].forEach((text) => {
      const td = document.createElement('td');
      td.textContent = text;
      row.appendChild(td);
    });
    recentSessions.appendChild(row);
  });

  renderIndividualFlags();
}

async function loadStats() {
  statusEl.textContent = '통계를 불러오는 중입니다.';
  try {
    // F5: 관리자 토큰 필요. URL의 ?token= 을 X-Stats-Token 헤더로 전달한다
    // (예: stats.html?token=XXXX). 헤더라 서버 접근 로그에 남지 않는다.
    const token = new URLSearchParams(location.search).get('token') || '';
    const headers = token ? { 'X-Stats-Token': token } : {};
    const response = await fetch('/api/stats?participant_prefix=demo-person-', { headers });
    if (response.status === 401) throw new Error('접근 토큰이 필요합니다 (URL 뒤에 ?token=... 를 붙이세요)');
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    renderStats(data);
    statusEl.textContent = data.exists
      ? `DB: ${data.database} · demo-person- 필터 적용`
      : '아직 data/chatlog.db가 없습니다. scripts/generate_demo_population.py를 실행해 샘플을 적재하세요.';
  } catch (error) {
    statusEl.textContent = `통계를 불러오지 못했습니다: ${error.message}`;
  }
}

tabButtons.forEach((button) => {
  button.addEventListener('click', () => setTab(button.dataset.tab));
});

filterButtons.forEach((button) => {
  button.setAttribute('aria-pressed', button.classList.contains('active') ? 'true' : 'false');
  button.addEventListener('click', () => setFilter(button.dataset.filter));
});

if (triageSearch) {
  triageSearch.addEventListener('input', () => {
    searchQuery = triageSearch.value.trim().toLocaleLowerCase('ko-KR');
    renderIndividualFlags();
  });
}

if (triageSort) {
  triageSort.addEventListener('change', () => {
    sortMode = triageSort.value;
    renderIndividualFlags();
  });
}

if (toggleDetailsButton) {
  toggleDetailsButton.addEventListener('click', () => {
    detailsExpanded = !detailsExpanded;
    toggleDetailsButton.textContent = detailsExpanded ? '전체 접기' : '전체 펼치기';
    renderIndividualFlags();
  });
}

if (exportCsvButton) {
  exportCsvButton.addEventListener('click', exportVisibleCsv);
}

tabButtons.forEach((button) => {
  button.setAttribute('aria-selected', button.classList.contains('active') ? 'true' : 'false');
});

const initialTab = ['overview', 'management', 'crisis'].includes(location.hash.slice(1))
  ? location.hash.slice(1)
  : 'overview';
setTab(initialTab);
loadStats();
