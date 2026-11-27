/* Session preview in the desktop WebEngine. Data is always scoped to one session. */
let showMouseHeat = false;
let playbackFit = 'contain';
function setPlaybackFit(value) {
  playbackFit = value;
  reviewVideo.style.objectFit = value;
  reviewDrawOverlay();
}
let reviewData = null;
let reviewLoadGeneration = 0;
let reviewStart = null;
let reviewEnd = null;
let reviewSelectedNote = null;
let reviewPoll = null;
let reviewBubbleTimer = null;
let reviewVisibleNote = null;
const reviewVideo = document.getElementById('review-video');

function reviewClock(seconds) {
  const n = Math.max(0, seconds || 0);
  return `${String(Math.floor(n / 60)).padStart(2, '0')}:${String(Math.floor(n % 60)).padStart(2, '0')}.${String(Math.floor(n % 1 * 1000)).padStart(3, '0')}`;
}

function reviewUpdateTime() {
  document.getElementById('review-time').textContent = reviewClock(reviewVideo.currentTime);
  document.getElementById('review-play').textContent = reviewVideo.paused ? '▶' : 'Ⅱ';
  document.querySelectorAll('.review-playhead').forEach(line => {
    line.style.left = `${Math.min(100, reviewVideo.currentTime / (reviewVideo.duration || 1) * 100)}%`;
  });
  const m = reviewData?.metrics || {};
  const counts = [m.total_clicks, null, Number.isFinite(reviewVideo.duration) ? reviewClock(reviewVideo.duration).split('.')[0] : null, m.mouse_scrolls_count, m.total_keystrokes, reviewData?.notes.length];
  document.querySelectorAll('#insights b').forEach((el,i)=>el.textContent=counts[i] ?? '—');
  reviewDrawOverlay();
  reviewShowComment();
}
reviewVideo.addEventListener('timeupdate', reviewUpdateTime);
reviewVideo.addEventListener('play', reviewUpdateTime);
reviewVideo.addEventListener('pause', reviewUpdateTime);
reviewVideo.addEventListener('loadedmetadata', reviewRenderTimeline);
new ResizeObserver(reviewDrawOverlay).observe(document.querySelector('.review-video-wrap'));
reviewVideo.addEventListener('error', () => {
  if (!reviewVideo.hasAttribute('src')) return;
  document.getElementById('review-message').textContent = 'Preview could not be decoded. Try selecting the session again.';
});

function reviewTogglePlay() { reviewVideo.paused ? reviewVideo.play() : reviewVideo.pause(); }
function reviewSkip(seconds) {
  reviewVideo.currentTime = Math.max(0, Math.min(reviewVideo.duration || Infinity,
    reviewVideo.currentTime + seconds));
}
function reviewStep(direction) {
  reviewVideo.pause();
  reviewVideo.currentTime = Math.max(0, Math.min(reviewVideo.duration || Infinity,
    reviewVideo.currentTime + direction / (reviewData?.fps || 30)));
}
function reviewMarkStart() { reviewStart = reviewVideo.currentTime; reviewEnd = null; reviewRangeLabel(); }
function reviewMarkEnd() { reviewEnd = Math.max(reviewStart ?? reviewVideo.currentTime, reviewVideo.currentTime); reviewRangeLabel(); }
document.addEventListener('keydown', event => {
  if (!document.getElementById('page-review').classList.contains('active') ||
      ['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement?.tagName)) return;
  if (event.code === 'Space') { event.preventDefault(); reviewTogglePlay(); }
  if (event.code === 'ArrowLeft') { event.preventDefault(); reviewStep(-1); }
  if (event.code === 'ArrowRight') { event.preventDefault(); reviewStep(1); }
});
function reviewRangeLabel() {
  document.getElementById('review-range-label').textContent = reviewStart === null ? 'Current frame' :
    `${reviewClock(reviewStart)} → ${reviewClock(reviewEnd ?? reviewStart)}`;
}
function reviewClearNote() {
  reviewSelectedNote = null; reviewStart = null; reviewEnd = null;
  document.getElementById('review-note-text').value = '';
  reviewRangeLabel();
}
function reviewOpenNoteEditor() {
  document.getElementById('review-note-editor').classList.add('visible');
  document.getElementById('review-note-text').focus();
}
function reviewCloseNoteEditor() {
  document.getElementById('review-note-editor').classList.remove('visible');
  reviewClearNote();
}

function reviewShowComment() {
  const bubble = document.getElementById('review-bubble');
  const note = reviewData?.notes.find(n => reviewVideo.currentTime >= n.start &&
    reviewVideo.currentTime <= n.end + Math.max(0.4, 1 / (reviewData.fps || 30)));
  if (!note) {
    if (reviewVisibleNote) { bubble.hidden = true; reviewVisibleNote = null; }
    return;
  }
  if (reviewVisibleNote === note.id) return;
  reviewVisibleNote = note.id;
  bubble.textContent = note.text;
  bubble.hidden = false;
  clearTimeout(reviewBubbleTimer);
  reviewBubbleTimer = setTimeout(() => { bubble.hidden = true; }, 5000);
}

function reviewDrawOverlay() {
  const canvas = document.getElementById('review-overlay');
  const bounds = canvas.getBoundingClientRect();
  const scaleFactor = window.devicePixelRatio || 1;
  canvas.width = Math.round(bounds.width * scaleFactor);
  canvas.height = Math.round(bounds.height * scaleFactor);
  const ctx = canvas.getContext('2d');
  ctx.scale(scaleFactor, scaleFactor);
  if (!reviewData || !reviewVideo.videoWidth || !reviewVideo.videoHeight) return;
  const scale = (playbackFit === 'cover' ? Math.max : Math.min)(bounds.width / reviewVideo.videoWidth, bounds.height / reviewVideo.videoHeight);
  const left = (bounds.width - reviewVideo.videoWidth * scale) / 2;
  const top = (bounds.height - reviewVideo.videoHeight * scale) / 2;
  if (showMouseHeat) {
    const g = reviewData.geometry || {};
    for (const e of reviewData.events) {
      if (e.type !== 'mouse_click' || !Number.isFinite(e.x) || !Number.isFinite(e.y)) continue;
      const x = left + (e.x-(g.left||0))*reviewVideo.videoWidth*scale/(g.width||reviewVideo.videoWidth);
      const y = top + (e.y-(g.top||0))*reviewVideo.videoHeight*scale/(g.height||reviewVideo.videoHeight);
      const spot = ctx.createRadialGradient(x,y,0,x,y,24);
      spot.addColorStop(0,'rgba(255,108,30,.5)'); spot.addColorStop(.5,'rgba(254,209,40,.22)'); spot.addColorStop(1,'rgba(0,200,230,0)');
      ctx.fillStyle=spot; ctx.fillRect(x-24,y-24,48,48);
    }
  }
  const now = reviewVideo.currentTime;
  reviewData.events.filter(e => e.type === 'mouse_click' && Number.isFinite(e.x) &&
    Number.isFinite(e.y) && now >= e.time && now <= e.time + 0.7).forEach(e => {
    const button = e.button.toLowerCase();
    const kind = button.includes('right') ? 'R' : button.includes('middle') ? 'M' : 'L';
    const color = kind === 'R' ? '#ef6c78' : kind === 'M' ? '#c890f3' : '#50a4ff';
    const g = reviewData.geometry || {};
    const x = left + (e.x - (g.left || 0)) * reviewVideo.videoWidth * scale / (g.width || reviewVideo.videoWidth);
    const y = top + (e.y - (g.top || 0)) * reviewVideo.videoHeight * scale / (g.height || reviewVideo.videoHeight);
    const radius = 19 + (now - e.time) * 17;
    ctx.beginPath(); ctx.arc(x, y, radius, 0, Math.PI * 2);
    ctx.strokeStyle = color; ctx.lineWidth = 3; ctx.stroke();
    ctx.fillStyle = color; ctx.font = 'bold 15px Inter, sans-serif';
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText(kind, x, y);
  });
}

async function loadReview() {
  const generation = ++reviewLoadGeneration;
  const sid = document.getElementById('review-session-select').value;
  const content = document.getElementById('review-content');
  const message = document.getElementById('review-message');
  clearTimeout(reviewPoll); reviewVideo.pause(); reviewVideo.removeAttribute('src'); reviewVideo.load();
  content.classList.add('no-session');
  message.textContent = ''; reviewData = null; reviewCloseNoteEditor();
  clearTimeout(reviewBubbleTimer); reviewVisibleNote = null;
  document.getElementById('review-bubble').hidden = true;
  document.getElementById('review-timeline').replaceChildren();
  reviewDrawOverlay();
  document.getElementById('review-video-placeholder').hidden = !!sid;
  document.querySelectorAll('.review-playlist-item').forEach(item => item.classList.toggle('active',
    item.querySelector('small')?.textContent.endsWith(' · ' + sid)));
  if (!sid) return;
  try {
    const data = await api('GET', `/api/sessions/${encodeURIComponent(sid)}/review`);
    if (generation !== reviewLoadGeneration) return;
    reviewData = {...data, sid};
    let cursor = data.next_cursor;
    while (cursor !== null && cursor !== undefined) {
      message.textContent = `Loading session events (${reviewData.events.length})…`;
      const page = await api('GET', `/api/sessions/${encodeURIComponent(sid)}/review?cursor=${cursor}`);
      if (generation !== reviewLoadGeneration) return;
      reviewData.events = reviewData.events.concat(page.events);
      cursor = page.next_cursor;
    }
    if (!data.has_video) { message.textContent = 'This session has no recording.'; return; }
    content.classList.remove('no-session');
    reviewRenderTimeline();
    reviewLoadPreview(sid);
  } catch (error) {
    if (generation === reviewLoadGeneration) {
      reviewData = null; message.textContent = 'Could not load all session events. Select the session to retry.';
    }
  }
}

async function reviewLoadPreview(sid) {
  const message = document.getElementById('review-message');
  try {
    const state = await api('GET', `/api/sessions/${encodeURIComponent(sid)}/review/preview`);
    if (sid !== reviewData?.sid) return;
    if (state.status === 'ready') {
      reviewVideo.src = `${API}/api/sessions/${encodeURIComponent(sid)}/review/preview.webm`;
      message.textContent = 'Preview ready';
    } else if (state.status === 'processing') {
      message.textContent = 'Preparing a compatible video preview with FFmpeg…';
      reviewPoll = setTimeout(() => reviewLoadPreview(sid), 1800);
    } else { message.textContent = state.error || 'Recording unavailable.'; }
  } catch (error) { message.textContent = 'Could not prepare video preview.'; }
}

function reviewRenderTimeline() {
  if (!reviewData) return;
  const duration = reviewVideo.duration || reviewData.events.reduce((end, e) => Math.max(end, e.time),
    reviewData.notes.reduce((end, n) => Math.max(end, n.end), 1));
  const root = document.getElementById('review-timeline');
  root.replaceChildren();
  const tracks = [
    ['Time', null, '#67c6ff'], ['Clicks', 'mouse_click', '#4f8ef7'],
    ['Scroll', 'mouse_scroll', '#ae8afa'], ['Keys', 'key_press', '#39c5ad'],
    ['Comments', 'notes', '#e9ad4f'], ['Fixations (future)', 'fixation', '#888'],
    ['Saccades (future)', 'saccade', '#888']
  ];
  tracks.forEach(([name, type, color]) => {
    const row = document.createElement('div'); row.className = type ? 'review-track' : 'review-ruler';
    const label = document.createElement('div'); label.className = 'review-label'; label.textContent = name;
    const lane = document.createElement('div'); lane.className = 'review-lane';
    lane.onclick = event => {
      if (event.target !== lane) return;
      reviewVideo.currentTime = Math.max(0, Math.min(duration, event.offsetX / lane.clientWidth * duration));
    };
    const head = document.createElement('div'); head.className = 'review-playhead'; lane.appendChild(head);
    if (!type) {
      for (let i = 0; i <= 5; i++) {
        const tick = document.createElement('span'); tick.className = 'review-tick';
        tick.style.left = `${i * 20}%`; tick.textContent = reviewClock(duration * i / 5).slice(0, 5);
        lane.appendChild(tick);
      }
    } else if (type === 'notes') {
      reviewData.notes.forEach(note => {
        const mark = document.createElement('button'); mark.className = 'review-range';
        mark.style.left = `${note.start / duration * 100}%`;
        mark.style.width = `${Math.max(1, (note.end - note.start) / duration * 100)}%`;
        mark.title = note.text;
        mark.onclick = () => {
          reviewSelectedNote = note.id; reviewStart = note.start; reviewEnd = note.end;
          document.getElementById('review-note-text').value = note.text;
          reviewVideo.currentTime = note.start; reviewRangeLabel();
        };
        lane.appendChild(mark);
      });
    } else {
      reviewData.events.filter(e => e.type === type).forEach(event => {
        const mark = document.createElement('button'); mark.className = 'review-marker';
        mark.style.left = `${event.time / duration * 100}%`;
        const button = (event.button || '').toLowerCase();
        mark.style.background = type === 'mouse_click'
          ? (button.includes('right') ? '#ef6c78' : button.includes('middle') ? '#c890f3' : '#50a4ff')
          : color;
        mark.title = `${reviewClock(event.time)} ${name}${event.button ? ': ' + event.button : ''}`;
        mark.onclick = () => { reviewVideo.currentTime = event.time; reviewVideo.pause(); };
        lane.appendChild(mark);
      });
    }
    row.append(label, lane); root.appendChild(row);
  });
  reviewUpdateTime();
}

async function reviewAddNote() {
  if (!reviewData) return;
  const input = document.getElementById('review-note-text');
  const text = input.value.trim();
  if (!text) { toast('Write a comment first', 'error'); return; }
  const frame = 1 / (reviewData.fps || 30);
  const start = reviewStart ?? Math.round(reviewVideo.currentTime / frame) * frame;
  const end = reviewEnd ?? start;
  try {
    const note = await api('POST', `/api/sessions/${encodeURIComponent(reviewData.sid)}/review/notes`,
      {id: reviewSelectedNote, text, start, end});
    const index = reviewData.notes.findIndex(item => item.id === note.id);
    if (index < 0) reviewData.notes.push(note); else reviewData.notes[index] = note;
    reviewCloseNoteEditor(); reviewRenderTimeline(); toast('Comment saved', 'success');
  } catch (error) { toast('Could not save comment', 'error'); }
}
