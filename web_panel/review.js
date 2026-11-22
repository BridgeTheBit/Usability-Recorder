/* Session preview in the desktop WebEngine. Data is always scoped to one session. */
let reviewData = null;
let reviewStart = null;
let reviewEnd = null;
let reviewSelectedNote = null;
let reviewPoll = null;
const reviewVideo = document.getElementById('review-video');

function reviewClock(seconds) {
  const n = Math.max(0, seconds || 0);
  return `${String(Math.floor(n / 60)).padStart(2, '0')}:${String(Math.floor(n % 60)).padStart(2, '0')}.${String(Math.floor(n % 1 * 1000)).padStart(3, '0')}`;
}

function reviewUpdateTime() {
  document.getElementById('review-time').textContent = reviewClock(reviewVideo.currentTime);
  document.getElementById('review-play').textContent = reviewVideo.paused ? '▶ Play' : '⏸ Pause';
  document.querySelectorAll('.review-playhead').forEach(line => {
    line.style.left = `${Math.min(100, reviewVideo.currentTime / (reviewVideo.duration || 1) * 100)}%`;
  });
}
reviewVideo.addEventListener('timeupdate', reviewUpdateTime);
reviewVideo.addEventListener('play', reviewUpdateTime);
reviewVideo.addEventListener('pause', reviewUpdateTime);
reviewVideo.addEventListener('loadedmetadata', reviewRenderTimeline);
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

async function loadReview() {
  const sid = document.getElementById('review-session-select').value;
  const content = document.getElementById('review-content');
  const message = document.getElementById('review-message');
  clearTimeout(reviewPoll); reviewVideo.pause(); reviewVideo.removeAttribute('src'); reviewVideo.load();
  content.style.display = 'none'; message.textContent = ''; reviewData = null; reviewClearNote();
  if (!sid) return;
  try {
    const data = await api('GET', `/api/sessions/${encodeURIComponent(sid)}/review`);
    if (sid !== document.getElementById('review-session-select').value) return;
    reviewData = {...data, sid};
    if (!data.has_video) { message.textContent = 'This session has no recording.'; return; }
    content.style.display = '';
    reviewRenderTimeline();
    reviewLoadPreview(sid);
  } catch (error) { message.textContent = 'Could not load session review.'; }
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
  const duration = reviewVideo.duration || Math.max(1, ...reviewData.events.map(e => e.time),
    ...reviewData.notes.map(n => n.end));
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
        mark.style.left = `${event.time / duration * 100}%`; mark.style.background = color;
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
    reviewClearNote(); reviewRenderTimeline(); toast('Comment saved', 'success');
  } catch (error) { toast('Could not save comment', 'error'); }
}
