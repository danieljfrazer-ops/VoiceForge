/* ============ VoiceForge frontend ============ */
"use strict";

const $ = (id) => document.getElementById(id);
const api = {
  async req(path, opts = {}) {
    const res = await fetch(path, opts);
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch {}
      throw new Error(detail);
    }
    return res.json();
  },
  status: () => api.req("/api/status"),
  voices: () => api.req("/api/voices"),
  createVoice: (form) => api.req("/api/voices", { method: "POST", body: form }),
  patchVoice: (id, body) => api.req(`/api/voices/${id}`, {
    method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
  deleteVoice: (id) => api.req(`/api/voices/${id}`, { method: "DELETE" }),
  generate: (id, body) => api.req(`/api/voices/${id}/generate`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
  deleteClip: (vid, cid) => api.req(`/api/voices/${vid}/clips/${cid}`, { method: "DELETE" }),
  transcribe: (form) => api.req("/api/transcribe", { method: "POST", body: form }),
};

/* ---------------- state ---------------- */
let voices = [];
let activeVoiceId = localStorage.getItem("activeVoice") || null;
let tweakDirty = false;      // composer sliders diverge from voice defaults
let modelReady = false;

const AVATAR_COLORS = ["#8b5cf6", "#ec4899", "#f59e0b", "#10b981", "#3b82f6", "#f43f5e", "#14b8a6", "#a855f7"];
const avatarColor = (id) => AVATAR_COLORS[[...id].reduce((a, c) => a + c.charCodeAt(0), 0) % AVATAR_COLORS.length];
const initials = (name) => name.trim().split(/\s+/).map(w => w[0]).slice(0, 2).join("").toUpperCase();

function activeVoice() { return voices.find(v => v.id === activeVoiceId) || null; }

/* ---------------- toast ---------------- */
let toastTimer;
function toast(msg, isError = false) {
  const el = $("toast");
  el.textContent = msg;
  el.className = "toast" + (isError ? " error" : "");
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, isError ? 5000 : 2600);
}

/* ---------------- model status ---------------- */
async function pollStatus() {
  try {
    const s = await api.status();
    const dot = document.querySelector("#modelStatus .dot");
    const txt = $("modelStatusText");
    if (s.tts_error) {
      dot.className = "dot error";
      txt.textContent = "Model failed to load";
      modelReady = false;
    } else if (s.tts_loaded) {
      dot.className = "dot";
      txt.textContent = `Local · ${s.engine || "TTS"} on ${s.device.toUpperCase()}`;
      modelReady = true;
      return; // stop polling
    } else {
      dot.className = "dot loading";
      txt.textContent = "Loading voice model…";
    }
  } catch {
    $("modelStatusText").textContent = "Connecting…";
  }
  setTimeout(pollStatus, 2500);
}

/* ---------------- audio recording helper ---------------- */
function pickMime() {
  const candidates = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"];
  for (const m of candidates) {
    if (window.MediaRecorder && MediaRecorder.isTypeSupported(m)) return m;
  }
  return "";
}

class Recorder {
  constructor() { this.stream = null; this.rec = null; this.chunks = []; this.ctx = null; this.analyser = null; }
  async start() {
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true } });
    this.chunks = [];
    const mime = pickMime();
    this.rec = new MediaRecorder(this.stream, mime ? { mimeType: mime } : undefined);
    this.rec.ondataavailable = (e) => { if (e.data.size) this.chunks.push(e.data); };
    this.rec.start(250);
    this.startedAt = Date.now();
    // analyser for waveform
    this.ctx = new (window.AudioContext || window.webkitAudioContext)();
    const src = this.ctx.createMediaStreamSource(this.stream);
    this.analyser = this.ctx.createAnalyser();
    this.analyser.fftSize = 512;
    src.connect(this.analyser);
  }
  async stop() {
    const rec = this.rec;
    const blob = await new Promise((resolve) => {
      rec.onstop = () => resolve(new Blob(this.chunks, { type: rec.mimeType || "audio/webm" }));
      rec.stop();
    });
    this.stream.getTracks().forEach(t => t.stop());
    if (this.ctx) { this.ctx.close().catch(() => {}); this.ctx = null; }
    return blob;
  }
  get seconds() { return (Date.now() - this.startedAt) / 1000; }
}

function extFor(blob) {
  if (blob.type.includes("mp4")) return "m4a";
  if (blob.type.includes("webm")) return "webm";
  if (blob.type.includes("ogg")) return "ogg";
  return "wav";
}

/* ---------------- sliders ---------------- */
function bindSlider(sliderId, labelId, digits = 2) {
  const slider = $(sliderId), label = $(labelId);
  const paint = () => {
    const min = +slider.min, max = +slider.max, val = +slider.value;
    slider.style.setProperty("--fill", `${((val - min) / (max - min)) * 100}%`);
    label.textContent = val.toFixed(digits);
  };
  slider.addEventListener("input", paint);
  paint();
  return { slider, paint, set(v) { slider.value = v; paint(); } };
}

const sldTemp = bindSlider("sldTemp", "valTemp");
const sldSpeed = bindSlider("sldSpeed", "valSpeed");
const wizTemp = bindSlider("wizSldTemp", "wizValTemp");
const wizSpeed = bindSlider("wizSldSpeed", "wizValSpeed");

function composerConfig() {
  return {
    temperature: +sldTemp.slider.value,
    speed: +sldSpeed.slider.value,
  };
}
function setComposerConfig(cfg) {
  sldTemp.set(cfg.temperature ?? 0.9);
  sldSpeed.set(cfg.speed ?? 1.0);
}

[sldTemp.slider, sldSpeed.slider].forEach(s =>
  s.addEventListener("input", () => { tweakDirty = true; }));

/* ---------------- render: voices ---------------- */
function renderVoices() {
  const row = $("voicesRow");
  row.querySelectorAll(".voice-chip:not(.add)").forEach(el => el.remove());
  const addBtn = $("btnNewVoice");
  for (const v of voices) {
    const chip = document.createElement("button");
    chip.className = "voice-chip" + (v.id === activeVoiceId ? " active" : "");
    chip.innerHTML = `<span class="chip-avatar" style="background:${avatarColor(v.id)}">${initials(v.name)}</span><span>${escapeHtml(v.name)}</span>`;
    chip.onclick = () => selectVoice(v.id);
    row.insertBefore(chip, addBtn);
  }
  const hasVoices = voices.length > 0;
  $("emptyState").hidden = hasVoices;
  $("composer").hidden = !hasVoices;
  $("clipsSection").hidden = !hasVoices;
  $("btnSettingsVoice").hidden = !hasVoices;
  if (hasVoices && !activeVoice()) selectVoice(voices[0].id);
  if (hasVoices) {
    $("composerVoiceName").textContent = activeVoice().name;
    $("clipsTitle").textContent = `Clips · ${activeVoice().name}`;
  }
}

function selectVoice(id) {
  activeVoiceId = id;
  localStorage.setItem("activeVoice", id);
  tweakDirty = false;
  const v = activeVoice();
  if (v) setComposerConfig(v.config || {});
  stopPlayback();
  renderVoices();
  renderClips();
}

/* ---------------- render: clips ---------------- */
const fmtTime = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
const fmtDate = (ts) => {
  const d = new Date(ts * 1000), now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  return sameDay ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
                 : d.toLocaleDateString([], { day: "numeric", month: "short" }) + " " +
                   d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
};
function escapeHtml(s) {
  return s.replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* shared player */
const player = new Audio();
let playingClipId = null;
player.addEventListener("ended", () => stopPlayback());
player.addEventListener("timeupdate", () => {
  if (!playingClipId) return;
  const card = document.querySelector(`.clip-card[data-id="${playingClipId}"]`);
  if (!card) return;
  const bar = card.querySelector(".clip-seek");
  const t = card.querySelector(".clip-time");
  if (bar && !bar.matches(":active") && player.duration) {
    bar.value = (player.currentTime / player.duration) * 100;
    bar.style.setProperty("--fill", bar.value + "%");
  }
  if (t) t.textContent = fmtTime(player.currentTime);
});

function stopPlayback() {
  player.pause();
  if (playingClipId) {
    const card = document.querySelector(`.clip-card[data-id="${playingClipId}"]`);
    if (card) { card.classList.remove("playing"); card.querySelector(".clip-play").textContent = "▶"; }
  }
  playingClipId = null;
}

function togglePlay(clip) {
  const card = document.querySelector(`.clip-card[data-id="${clip.id}"]`);
  if (!card) return;
  if (playingClipId === clip.id) {
    if (player.paused) { player.play().catch(() => {}); card.querySelector(".clip-play").textContent = "❚❚"; }
    else { player.pause(); card.querySelector(".clip-play").textContent = "▶"; }
    return;
  }
  stopPlayback();
  playingClipId = clip.id;
  player.src = `/api/voices/${clip.voice_id}/clips/${clip.id}/audio`;
  card.classList.add("playing");
  card.querySelector(".clip-play").textContent = "❚❚";
  player.play().catch(() => {
    // iOS blocks autoplay outside a tap gesture — reset so the user can tap play
    card.classList.remove("playing");
    card.querySelector(".clip-play").textContent = "▶";
    playingClipId = null;
  });
}

function renderClips() {
  const v = activeVoice();
  const list = $("clipsList");
  list.innerHTML = "";
  const clips = v ? (v.clips || []) : [];
  $("clipsEmpty").hidden = clips.length > 0;
  for (const clip of clips) {
    const card = document.createElement("div");
    card.className = "clip-card";
    card.dataset.id = clip.id;
    card.innerHTML = `
      <div class="clip-main">
        <button class="clip-play">▶</button>
        <div class="clip-body">
          <div class="clip-text">${escapeHtml(clip.text)}</div>
          <div class="clip-meta">
            ${clip.kind === "preview" ? '<span class="clip-badge">preview</span>' : ""}
            <span>${fmtTime(clip.duration || 0)}</span><span>·</span><span>${fmtDate(clip.created)}</span>
          </div>
        </div>
        <div class="clip-actions">
          <button class="clip-icon-btn dl" title="Download">↓</button>
          <button class="clip-icon-btn del" title="Delete">✕</button>
        </div>
      </div>
      <div class="clip-progress">
        <input type="range" class="clip-seek" min="0" max="100" value="0" step="0.1" style="--fill:0%">
        <span class="clip-time">0:00</span>
      </div>`;
    card.querySelector(".clip-play").onclick = () => togglePlay(clip);
    card.querySelector(".clip-seek").addEventListener("input", (e) => {
      if (playingClipId === clip.id && player.duration) {
        player.currentTime = (e.target.value / 100) * player.duration;
        e.target.style.setProperty("--fill", e.target.value + "%");
      }
    });
    card.querySelector(".dl").onclick = () => {
      const a = document.createElement("a");
      a.href = `/api/voices/${clip.voice_id}/clips/${clip.id}/audio`;
      a.download = `${activeVoice()?.name || "clip"}-${clip.id}.wav`;
      a.click();
    };
    card.querySelector(".del").onclick = async () => {
      if (!confirm("Delete this clip?")) return;
      try {
        await api.deleteClip(clip.voice_id, clip.id);
        if (playingClipId === clip.id) stopPlayback();
        await refreshVoices(false);
        renderClips();
      } catch (e) { toast(e.message, true); }
    };
    list.appendChild(card);
  }
}

/* ---------------- generation ---------------- */
$("genText").addEventListener("input", () => {
  $("charCount").textContent = `${$("genText").value.length} / 3000`;
});

$("btnGenerate").onclick = async () => {
  const v = activeVoice();
  const text = $("genText").value.trim();
  if (!v) return;
  if (!text) { toast("Type or dictate some text first"); return; }
  if (!modelReady) { toast("Voice model is still loading — one moment…"); return; }
  const btn = $("btnGenerate");
  btn.classList.add("working");
  btn.disabled = true;
  try {
    const body = { text, kind: "clip" };
    if (tweakDirty) body.config = composerConfig();
    await api.generate(v.id, body);
    await refreshVoices(false);
    renderClips();
    // auto-play the fresh clip
    const fresh = activeVoice()?.clips?.[0];
    if (fresh) togglePlay(fresh);
    $("genText").value = "";
    $("charCount").textContent = "0 / 3000";
  } catch (e) {
    toast(e.message, true);
  } finally {
    btn.classList.remove("working");
    btn.disabled = false;
  }
};

/* tweak panel */
$("btnTweak").onclick = () => { $("tweakPanel").hidden = !$("tweakPanel").hidden; };
$("btnTweakReset").onclick = () => {
  const v = activeVoice();
  if (v) { setComposerConfig(v.config || {}); tweakDirty = false; }
};
$("btnTweakSave").onclick = async () => {
  const v = activeVoice();
  if (!v) return;
  try {
    await api.patchVoice(v.id, { config: composerConfig() });
    await refreshVoices(false);
    tweakDirty = false;
    toast("Saved as voice defaults");
  } catch (e) { toast(e.message, true); }
};

/* ---------------- dictation ---------------- */
let dictRec = null;
$("btnDictate").onclick = async () => {
  const btn = $("btnDictate");
  if (dictRec) { // stop & transcribe
    const rec = dictRec; dictRec = null;
    btn.classList.remove("recording");
    btn.classList.add("busy");
    try {
      const blob = await rec.stop();
      if (rec.seconds < 0.6) { toast("Too short — hold on a bit longer"); return; }
      const form = new FormData();
      form.append("audio", blob, `dictation.${extFor(blob)}`);
      const { text } = await api.transcribe(form);
      if (text) {
        const area = $("genText");
        area.value = (area.value.trim() ? area.value.trim() + " " : "") + text;
        area.dispatchEvent(new Event("input"));
      } else {
        toast("Didn't catch that — try again");
      }
    } catch (e) {
      toast(e.message, true);
    } finally {
      btn.classList.remove("busy");
    }
    return;
  }
  try {
    dictRec = new Recorder();
    await dictRec.start();
    btn.classList.add("recording");
    toast("Listening… tap again to stop");
  } catch (e) {
    dictRec = null;
    toast(micErrorMessage(e), true);
  }
};

function micErrorMessage(e) {
  if (location.protocol === "http:" && !["localhost", "127.0.0.1"].includes(location.hostname)) {
    return "Microphone needs HTTPS — open the https:// address shown in the terminal";
  }
  if (e.name === "NotAllowedError") return "Microphone access denied — allow it in your browser settings";
  return "Could not access microphone: " + e.message;
}

/* ---------------- wizard ---------------- */
const wiz = {
  step: 1,
  name: "",
  blob: null,
  voiceId: null,   // set once created on server
  rec: null,
  raf: null,
  timerInt: null,
};

function wizShow(step) {
  wiz.step = step;
  ["wizStep1", "wizStep2", "wizStep3", "wizBusy"].forEach((id, i) => {
    $(id).hidden = (i + 1) !== step && !(step === 4 && id === "wizBusy");
  });
  document.querySelectorAll(".step-dot").forEach((d, i) => {
    d.className = "step-dot" + (i + 1 < step ? " done" : i + 1 === step ? " active" : "");
  });
  $("wizBack").style.visibility = (step === 1 || step === 4) ? "hidden" : "visible";
}

function openWizard() {
  wiz.name = ""; wiz.blob = null; wiz.voiceId = null;
  $("wizName").value = "";
  $("wizNext1").disabled = true;
  $("recReview").hidden = true;
  $("recHint").textContent = "Tap to start recording";
  $("recTimer").textContent = "0:00";
  $("previewPlayerWrap").innerHTML = "";
  wizTemp.set(0.9); wizSpeed.set(1.0);
  clearCanvas();
  $("wizard").hidden = false;
  wizShow(1);
  setTimeout(() => $("wizName").focus(), 350);
}

async function closeWizard() {
  if (wiz.rec) { try { await wiz.rec.stop(); } catch {} stopMeter(); wiz.rec = null; }
  // voice created but wizard abandoned before finalize -> clean up
  if (wiz.voiceId) {
    try { await api.deleteVoice(wiz.voiceId); } catch {}
    wiz.voiceId = null;
    await refreshVoices(false);
    renderVoices(); renderClips();
  }
  $("wizard").hidden = true;
}

$("btnNewVoice").onclick = openWizard;
$("btnEmptyNew").onclick = openWizard;
$("wizCancel").onclick = closeWizard;
$("wizBack").onclick = () => { if (wiz.step === 2) wizShow(1); else if (wiz.step === 3) wizShow(2); };

$("wizName").addEventListener("input", () => {
  $("wizNext1").disabled = !$("wizName").value.trim();
});
$("wizName").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && $("wizName").value.trim()) { wizShow(2); }
});
$("wizNext1").onclick = () => { wiz.name = $("wizName").value.trim(); wizShow(2); };

/* --- step 2: recording with waveform --- */
const canvas = $("recCanvas");
const cctx = canvas.getContext("2d");

function clearCanvas() {
  cctx.fillStyle = "rgba(0,0,0,0)";
  cctx.clearRect(0, 0, canvas.width, canvas.height);
}

function drawMeter() {
  const analyser = wiz.rec?.analyser;
  if (!analyser) return;
  const data = new Uint8Array(analyser.frequencyBinCount);
  analyser.getByteFrequencyData(data);
  cctx.clearRect(0, 0, canvas.width, canvas.height);
  const bars = 48, step = Math.floor(data.length / bars);
  const bw = canvas.width / bars;
  for (let i = 0; i < bars; i++) {
    const v = data[i * step] / 255;
    const h = Math.max(4, v * canvas.height * 0.9);
    const x = i * bw + bw * 0.18;
    const grad = cctx.createLinearGradient(0, canvas.height, 0, 0);
    grad.addColorStop(0, "#8b5cf6");
    grad.addColorStop(1, "#22d3ee");
    cctx.fillStyle = grad;
    cctx.beginPath();
    if (cctx.roundRect) cctx.roundRect(x, (canvas.height - h) / 2, bw * 0.64, h, 4);
    else cctx.rect(x, (canvas.height - h) / 2, bw * 0.64, h);
    cctx.fill();
  }
  wiz.raf = requestAnimationFrame(drawMeter);
}

function stopMeter() {
  cancelAnimationFrame(wiz.raf);
  clearInterval(wiz.timerInt);
}

$("btnRecord").onclick = async () => {
  const btn = $("btnRecord");
  if (wiz.rec) { // stop
    stopMeter();
    const rec = wiz.rec; wiz.rec = null;
    btn.classList.remove("recording");
    let blob;
    try { blob = await rec.stop(); } catch { return; }
    if (rec.seconds < 4) {
      $("recHint").textContent = "Too short — aim for at least 10 seconds";
      clearCanvas();
      return;
    }
    wiz.blob = blob;
    $("recPlayback").src = URL.createObjectURL(blob);
    $("recReview").hidden = false;
    $("recHint").textContent = `Recorded ${Math.round(rec.seconds)}s — listen back below`;
    return;
  }
  try {
    wiz.rec = new Recorder();
    await wiz.rec.start();
  } catch (e) {
    wiz.rec = null;
    toast(micErrorMessage(e), true);
    return;
  }
  $("recReview").hidden = true;
  btn.classList.add("recording");
  $("recHint").textContent = "Recording — tap again to stop";
  drawMeter();
  wiz.timerInt = setInterval(() => {
    if (wiz.rec) $("recTimer").textContent = fmtTime(wiz.rec.seconds);
  }, 250);
};

$("btnRerecord").onclick = () => {
  wiz.blob = null;
  $("recReview").hidden = true;
  $("recHint").textContent = "Tap to start recording";
  $("recTimer").textContent = "0:00";
  clearCanvas();
};

$("fileUpload").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  wiz.blob = file;
  $("recPlayback").src = URL.createObjectURL(file);
  $("recReview").hidden = false;
  $("recHint").textContent = `Using uploaded file: ${file.name}`;
});

/* --- step 2 -> create voice on server --- */
$("wizNext2").onclick = async () => {
  if (!wiz.blob) return;
  wizShow(4);
  $("wizBusyTitle").textContent = "Cloning voice…";
  $("wizBusyHint").textContent = "Processing your sample locally.";
  try {
    const form = new FormData();
    form.append("name", wiz.name);
    const name = wiz.blob.name || `sample.${extFor(wiz.blob)}`;
    form.append("audio", wiz.blob, name);
    const voice = await api.createVoice(form);
    wiz.voiceId = voice.id;
    wizShow(3);
  } catch (e) {
    toast(e.message, true);
    wizShow(2);
  }
};

/* --- step 3: preview + finalize --- */
$("btnPreview").onclick = async () => {
  if (!wiz.voiceId) return;
  if (!modelReady) { toast("Voice model is still loading — one moment…"); return; }
  const btn = $("btnPreview");
  btn.disabled = true;
  btn.textContent = "Generating preview…";
  try {
    const clip = await api.generate(wiz.voiceId, {
      text: `Hello! This is ${wiz.name}. My voice has been cloned, and this is how I sound.`,
      kind: "preview",
      config: {
        temperature: +wizTemp.slider.value,
        speed: +wizSpeed.slider.value,
      },
    });
    const wrap = $("previewPlayerWrap");
    wrap.innerHTML = "";
    const audio = document.createElement("audio");
    audio.controls = true;
    audio.src = `/api/voices/${wiz.voiceId}/clips/${clip.id}/audio`;
    wrap.appendChild(audio);
    audio.play().catch(() => {});
  } catch (e) {
    toast(e.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = "▶︎ Hear a preview";
  }
};

$("wizFinish").onclick = async () => {
  if (!wiz.voiceId) return;
  try {
    await api.patchVoice(wiz.voiceId, {
      finalized: true,
      config: {
        temperature: +wizTemp.slider.value,
        speed: +wizSpeed.slider.value,
      },
    });
    const id = wiz.voiceId;
    wiz.voiceId = null;              // prevent cleanup deletion
    $("wizard").hidden = true;
    await refreshVoices(false);
    selectVoice(id);
    toast(`Voice “${wiz.name}” is ready 🎉`);
  } catch (e) { toast(e.message, true); }
};

/* ---------------- voice settings sheet ---------------- */
$("btnSettingsVoice").onclick = () => {
  const v = activeVoice();
  if (!v) return;
  $("vsName").value = v.name;
  $("vsSample").src = `/api/voices/${v.id}/sample`;
  $("vsMeta").textContent =
    `Sample ${Math.round(v.sample_duration)}s · ${v.clips?.length || 0} clips · created ${fmtDate(v.created)}`;
  $("voiceSheet").hidden = false;
};

$("vsClose").onclick = async () => {
  const v = activeVoice();
  const newName = $("vsName").value.trim();
  if (v && newName && newName !== v.name) {
    try { await api.patchVoice(v.id, { name: newName }); await refreshVoices(false); renderVoices(); }
    catch (e) { toast(e.message, true); }
  }
  $("vsSample").pause();
  $("voiceSheet").hidden = true;
};

$("vsDelete").onclick = async () => {
  const v = activeVoice();
  if (!v) return;
  if (!confirm(`Delete “${v.name}” and all its clips? This can't be undone.`)) return;
  try {
    await api.deleteVoice(v.id);
    $("voiceSheet").hidden = true;
    activeVoiceId = null;
    localStorage.removeItem("activeVoice");
    await refreshVoices();
  } catch (e) { toast(e.message, true); }
};

/* close sheets when tapping the dimmed backdrop */
$("voiceSheet").addEventListener("click", (e) => { if (e.target === $("voiceSheet")) $("vsClose").click(); });
$("wizard").addEventListener("click", (e) => { if (e.target === $("wizard")) closeWizard(); });

/* ---------------- boot ---------------- */
async function refreshVoices(rerender = true) {
  voices = await api.voices();
  if (rerender) { renderVoices(); renderClips(); }
}

(async function boot() {
  try { await refreshVoices(); }
  catch { toast("Cannot reach the server", true); }
  pollStatus();
})();
