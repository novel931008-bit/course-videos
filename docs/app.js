(() => {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const WEEK = ['日', '一', '二', '三', '四', '五', '六'];
  const HUES = [212, 152, 24, 276, 338, 188, 44, 108, 0, 250];
  const state = { data: null, q: '', subject: '', teacher: '', desc: true };

  // ---------- helpers ----------
  function el(tag, attrs, ...children) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === 'class') e.className = v;
      else if (k === 'style') e.style.cssText = v;
      else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
      else e.setAttribute(k, v === true ? '' : v);
    }
    for (const c of children.flat()) if (c != null && c !== false) e.append(c);
    return e;
  }

  function hueOf(text) {
    let h = 0;
    for (const ch of text) h = (h * 31 + ch.codePointAt(0)) >>> 0;
    return HUES[h % HUES.length];
  }

  function parseDate(s) {
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s || '');
    return m ? new Date(+m[1], +m[2] - 1, +m[3]) : null;
  }

  function todayStr() {
    const d = new Date();
    const p = (n) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
  }

  const isHttp = (u) => /^https?:\/\//i.test(u || '');
  const uniq = (arr) => [...new Set(arr.filter(Boolean))].sort((a, b) => a.localeCompare(b, 'zh-Hant'));

  function parseStart(t) {
    if (!t) return 0;
    if (/^\d+$/.test(t)) return +t;
    const m = /^(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$/.exec(t);
    return m ? (+m[1] || 0) * 3600 + (+m[2] || 0) * 60 + (+m[3] || 0) : 0;
  }

  // 支援 youtu.be/ID、youtube.com/watch?v=ID、/live/ID、/shorts/ID、/embed/ID
  function youtube(u) {
    let url;
    try { url = new URL(u); } catch { return null; }
    const host = url.hostname.replace(/^(www|m|music)\./, '');
    let id = null;
    if (host === 'youtu.be') {
      id = url.pathname.split('/')[1];
    } else if (host === 'youtube.com' || host === 'youtube-nocookie.com') {
      if (url.pathname === '/watch') id = url.searchParams.get('v');
      else id = (/^\/(?:embed|shorts|live|v)\/([^/]+)/.exec(url.pathname) || [])[1];
    }
    if (!id || !/^[\w-]{11}$/.test(id)) return null;
    return { id, start: parseStart(url.searchParams.get('t') || url.searchParams.get('start')) };
  }

  // ---------- URL 參數（讓篩選結果可以分享） ----------
  function readParams() {
    const p = new URLSearchParams(location.search);
    state.q = p.get('q') || '';
    state.subject = p.get('subject') || '';
    state.teacher = p.get('teacher') || '';
    state.desc = p.get('sort') !== 'asc';
  }

  function writeParams() {
    const p = new URLSearchParams();
    if (state.q) p.set('q', state.q);
    if (state.subject) p.set('subject', state.subject);
    if (state.teacher) p.set('teacher', state.teacher);
    if (!state.desc) p.set('sort', 'asc');
    const qs = p.toString();
    history.replaceState(null, '', qs ? `?${qs}` : location.pathname);
  }

  // ---------- 畫面 ----------
  function renderMeta() {
    const d = state.data;
    const title = d.title || '課程影片總覽';
    document.title = title;
    $('site-title').textContent = title;
    $('site-desc').textContent = d.description || '';
    $('site-desc').hidden = !d.description;
    const parts = [`共 ${d.courses.length} 堂課`];
    const u = d.updatedAt ? new Date(d.updatedAt) : null;
    if (u && !isNaN(u)) {
      parts.push(`最後更新：${u.toLocaleString('zh-TW', { dateStyle: 'medium', timeStyle: 'short', hour12: false })}`);
    }
    $('meta').textContent = parts.join(' · ');
  }

  function fillSelect(sel, values, allLabel) {
    sel.replaceChildren(el('option', { value: '' }, allLabel), ...values.map((v) => el('option', { value: v }, v)));
  }

  function buildFilters() {
    const subjects = uniq(state.data.courses.map((c) => c.subject));
    const teachers = uniq(state.data.courses.map((c) => c.teacher));
    fillSelect($('f-subject'), subjects, '全部科別');
    fillSelect($('f-teacher'), teachers, '全部老師');
    if (!subjects.includes(state.subject)) state.subject = '';
    if (!teachers.includes(state.teacher)) state.teacher = '';
    $('f-subject').value = state.subject;
    $('f-teacher').value = state.teacher;
    $('f-teacher').hidden = teachers.length === 0;
    $('q').value = state.q;
    updateSortLabel();
  }

  function updateSortLabel() {
    $('sort').textContent = state.desc ? '日期：新 → 舊' : '日期：舊 → 新';
  }

  function dateCell(s) {
    const d = parseDate(s);
    if (!d) return el('td', { class: 'c-date' }, el('span', { class: 'date-main' }, s || '—'));
    return el('td', { class: 'c-date' },
      el('time', { datetime: s },
        el('span', { class: 'date-main' }, `${d.getMonth() + 1}/${d.getDate()}（${WEEK[d.getDay()]}）`),
        el('span', { class: 'date-sub' }, String(d.getFullYear()))));
  }

  function videoCell(c, today) {
    if (!isHttp(c.url)) {
      return el('span', { class: 'muted' }, c.date > today ? '尚未上課' : '影片尚未上傳');
    }
    const yt = youtube(c.url);
    if (!yt) {
      return el('div', { class: 'video-links' },
        el('a', { href: c.url, target: '_blank', rel: 'noopener' }, '開啟影片 ↗'));
    }
    const label = [c.subject, c.unit].filter(Boolean).join('：') || '上課影片';
    const play = () => openPlayer(yt, label, c.url);
    return el('div', { class: 'video' },
      el('button', { class: 'thumb', type: 'button', 'aria-label': `播放：${label}`, onclick: play },
        el('img', { src: `https://i.ytimg.com/vi/${yt.id}/mqdefault.jpg`, alt: '', loading: 'lazy', width: 320, height: 180 })),
      el('div', { class: 'video-links' },
        el('button', { type: 'button', onclick: play }, '▶ 在此播放'),
        el('a', { href: c.url, target: '_blank', rel: 'noopener' }, 'YouTube ↗')));
  }

  function showState(message, action) {
    const box = $('state');
    box.replaceChildren(el('div', null, message), action || '');
    box.hidden = false;
  }

  function clearFilters() {
    state.q = state.subject = state.teacher = '';
    $('q').value = $('f-subject').value = $('f-teacher').value = '';
    update();
  }

  function render() {
    const words = state.q.trim().toLowerCase().split(/\s+/).filter(Boolean);
    const list = state.data.courses
      .map((c, i) => ({ ...c, _i: i }))
      .filter((c) => {
        if (state.subject && c.subject !== state.subject) return false;
        if (state.teacher && c.teacher !== state.teacher) return false;
        if (!words.length) return true;
        const d = parseDate(c.date);
        const hay = [c.date, d ? `${d.getMonth() + 1}/${d.getDate()}` : '', c.subject, c.unit, c.teacher]
          .join(' ').toLowerCase();
        return words.every((w) => hay.includes(w));
      })
      // 依日期排序；同一天的課維持編輯器裡的順序
      .sort((a, b) => (a.date === b.date ? a._i - b._i : (a.date < b.date ? -1 : 1) * (state.desc ? -1 : 1)));

    const today = todayStr();
    const rows = [];
    let prev = null;
    let band = false;
    for (const c of list) {
      const first = c.date !== prev;
      if (first && prev !== null) band = !band;
      prev = c.date;
      rows.push(el('tr', { class: [first ? 'first' : 'repeat', band ? 'band' : ''].join(' ').trim() },
        dateCell(c.date),
        el('td', { class: 'c-subject' },
          c.subject ? el('span', { class: 'badge', style: `--h:${hueOf(c.subject)}` }, c.subject) : el('span', { class: 'muted' }, '—')),
        el('td', { class: 'c-unit' }, el('span', { class: 'unit' }, c.unit || '—')),
        el('td', { class: 'c-teacher' }, c.teacher || el('span', { class: 'muted' }, '—')),
        el('td', { class: 'c-video' }, videoCell(c, today))));
    }
    $('rows').replaceChildren(...rows);

    const total = state.data.courses.length;
    $('count').textContent = list.length !== total ? `找到 ${list.length} 堂（共 ${total} 堂）` : '';
    if (!total) {
      showState('目前還沒有課程資料。');
    } else if (!list.length) {
      showState('沒有符合條件的課程。',
        el('p', null, el('button', { type: 'button', class: 'link-btn', onclick: clearFilters }, '清除搜尋與篩選')));
    } else {
      $('state').hidden = true;
    }
  }

  function update() {
    writeParams();
    render();
  }

  // ---------- 影片播放視窗 ----------
  function openPlayer(yt, title, href) {
    $('player-title').textContent = title;
    $('player-yt').href = href;
    const src = `https://www.youtube-nocookie.com/embed/${yt.id}?autoplay=1&rel=0${yt.start ? `&start=${yt.start}` : ''}`;
    $('player-frame').replaceChildren(el('iframe', {
      src, title,
      allow: 'accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share',
      allowfullscreen: true,
      referrerpolicy: 'strict-origin-when-cross-origin',
    }));
    $('player').showModal();
  }

  function bind() {
    let timer;
    $('q').addEventListener('input', (e) => {
      clearTimeout(timer);
      timer = setTimeout(() => { state.q = e.target.value; update(); }, 120);
    });
    $('f-subject').addEventListener('change', (e) => { state.subject = e.target.value; update(); });
    $('f-teacher').addEventListener('change', (e) => { state.teacher = e.target.value; update(); });
    $('sort').addEventListener('click', () => { state.desc = !state.desc; updateSortLabel(); update(); });

    // 關閉時移除 iframe，影片才會停止播放
    const dlg = $('player');
    const stop = () => $('player-frame').replaceChildren();
    const closePlayer = () => { dlg.close(); stop(); };
    dlg.addEventListener('close', stop);
    dlg.addEventListener('click', (e) => { if (e.target === dlg) closePlayer(); });
    $('player-close').addEventListener('click', closePlayer);
  }

  async function load() {
    readParams();
    try {
      const res = await fetch('courses.json', { cache: 'no-cache' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      const str = (v) => String(v ?? '').trim();
      data.courses = (Array.isArray(data.courses) ? data.courses : [])
        .filter((c) => c && typeof c === 'object')
        .map((c) => ({ date: str(c.date), subject: str(c.subject), unit: str(c.unit), teacher: str(c.teacher), url: str(c.url) }));
      state.data = data;
    } catch (err) {
      $('meta').textContent = '';
      showState(`無法載入課程資料（${err.message}），請稍後重新整理頁面。`);
      return;
    }
    renderMeta();
    buildFilters();
    bind();
    render();
  }

  load();
})();
