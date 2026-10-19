/*
 * 상태 전환
 *   idle ──클릭──▶ listening ◀──(침묵)──▶ speaking
 *     ▲               │  클릭(종료)            │ 클릭(종료)
 *     └──3초──  stopping ◀──────────────────────┘
 *
 * - 텍스트: 브라우저 SpeechRecognition (ko-KR, continuous + interim). 끊기면 자동 재시작해 마이크 세션 유지.
 * - 음량/VAD: Web Audio AnalyserNode. 말 시작/멈춤은 상태(listening ↔ speaking)만 바꾸고 세션은 끊지 않음.
 * - 음성 명령: "아니 다시 할게요" / "다시 할게" / "처음부터 다시" → 입력창 전체 삭제 후 listening 유지.
 * - 입력창을 직접 지우거나 고쳐도 마이크 상태와 독립: 이후 음성은 현재 입력창 내용 뒤에 이어서 입력.
 */
(() => {
  const input = document.getElementById('userMemory');
  const btn = document.getElementById('micBtn');
  const cluster = document.getElementById('micCluster');
  const statusEl = document.getElementById('micStatus');
  const memoryForm = document.getElementById('colorArchForm');
  if (!input || !btn || !cluster) return;
  const bars = Array.from(cluster.querySelectorAll('.mic-wave i'));

  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const idlePlaceholder = input.placeholder;

  const STOP_HOLD_MS = 3000;       // stopping(빨간불) 유지 시간
  const SPEAK_HANGOVER_MS = 700;   // 이 시간 동안 소리가 없으면 speaking → listening
  const RESET_COMMANDS = ['아니다시할게', '다시할게', '처음부터다시'];

  let state = 'idle';
  let starting = false;
  let sessionToken = 0;
  let recWanted = false;
  let rec = null;
  let recStartedAt = 0;
  let rapidEnds = 0;
  let restartTimer = null;
  let stopTimer = null;
  let rafId = 0;
  let baseText = '';
  let finalText = '';

  let stream = null;
  let audioCtx = null;
  let analyser = null;
  let timeBuf = null;
  let freqBuf = null;
  let noAnalyser = false;
  let noiseFloor = 0.01;
  let lastVoiceAt = -1e9;
  let level = 0;

  function notice(message) {
    if (typeof showInlineNotice === 'function') showInlineNotice(message);
  }

  /* ---------- 상태 ---------- */
  function setState(next) {
    if (state === next) return;
    state = next;
    btn.dataset.state = next;
    cluster.dataset.state = next;
    const active = next === 'listening' || next === 'speaking';
    btn.setAttribute('aria-pressed', String(active));
    btn.setAttribute('aria-label', active ? '음성 입력 끝내기' : '음성으로 입력하기');
    btn.title = active ? '음성 입력 끝내기' : '음성으로 입력하기';
    input.placeholder = active ? '듣고 있어요… 편하게 말씀해 주세요.' : idlePlaceholder;
    if (statusEl) {
      statusEl.textContent = ({
        idle: '',
        listening: '음성 입력 대기 중',
        speaking: '음성 입력 중',
        stopping: '음성 입력을 종료했어요'
      })[next];
    }
  }

  function resetBars(scale) {
    bars.forEach((bar) => { bar.style.transform = 'scaleY(' + scale + ')'; });
    btn.style.setProperty('--level', '0');
    level = 0;
  }

  /* ---------- 텍스트 ---------- */
  function normalize(text) {
    return text.replace(/[\s.,!?~…·'"“”‘’]/g, '');
  }

  function isResetCommand(text) {
    const n = normalize(text);
    return RESET_COMMANDS.some((cmd) => n.includes(cmd));
  }

  function fireInput() {
    input.dispatchEvent(new Event('input', { bubbles: true }));
  }

  function writeText(spoken) {
    input.value = [baseText, spoken].filter(Boolean).join(' ');
    fireInput();
  }

  function markVoice() {
    lastVoiceAt = performance.now();
  }

  /* ---------- 음성 인식 (끊겨도 세션 유지) ---------- */
  function startRecognition() {
    if (!recWanted || !SR || rec) return;
    baseText = input.value.trim();
    finalText = '';

    const r = new SR();
    r.lang = 'ko-KR';
    r.interimResults = true;
    r.continuous = true;
    r.maxAlternatives = 1;
    rec = r;
    recStartedAt = Date.now();

    r.onspeechstart = () => { if (r === rec) markVoice(); };
    r.onresult = (event) => {
      if (r !== rec || r._ignore) return;
      markVoice();
      let interim = '';
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const piece = event.results[i][0].transcript;
        if (event.results[i].isFinal) finalText += piece;
        else interim += piece;
      }
      const spoken = (finalText + interim).trim();

      if (isResetCommand(spoken)) {
        input.value = '';
        fireInput();
        recycleRecognition(150);   // 남은 인식 결과를 버리고 새로 시작 (마이크는 계속 켜짐)
        return;
      }
      writeText(spoken);
    };
    r.onerror = (event) => {
      if (r !== rec) return;
      const err = event.error;
      if (err === 'no-speech' || err === 'aborted') return;
      if (err === 'audio-capture' && analyser) {
        // 일부 브라우저는 인식과 음량 측정이 마이크를 동시에 못 쓸 수 있음 → 음량은 추정치로 대체
        releaseAudio();
        noAnalyser = true;
        return;
      }
      if (err === 'not-allowed' || err === 'service-not-allowed') {
        fatalStop('마이크 권한이 필요해요. 브라우저 주소창의 권한 설정에서 마이크를 허용해 주세요.');
      } else if (err === 'audio-capture') {
        fatalStop('사용 가능한 마이크를 찾지 못했어요. 마이크 연결을 확인해 주세요.');
      } else if (err === 'network') {
        fatalStop('음성 인식 서버에 연결하지 못했어요. 잠시 후 다시 시도해 주세요.');
      } else if (err === 'language-not-supported') {
        fatalStop('이 브라우저는 한국어 음성 인식을 지원하지 않아요.');
      }
    };
    r.onend = () => {
      if (r !== rec) return;
      rec = null;
      if (!recWanted) return;
      // 침묵으로 인한 종료는 정상: 곧바로 이어서 듣는다. 시작 직후 반복 실패만 중단.
      rapidEnds = (Date.now() - recStartedAt < 1000) ? rapidEnds + 1 : 0;
      if (rapidEnds > 5) {
        fatalStop('음성 인식이 계속 끊겨요. 잠시 후 다시 시도해 주세요.');
        return;
      }
      clearTimeout(restartTimer);
      restartTimer = setTimeout(startRecognition, rapidEnds > 1 ? 400 : 120);
    };

    try {
      r.start();
    } catch (err) {
      rec = null;
      clearTimeout(restartTimer);
      restartTimer = setTimeout(startRecognition, 400);
    }
  }

  // 현재 인식을 버리고(입력창 내용은 유지) 잠시 후 새 인식으로 이어간다.
  function recycleRecognition(delay) {
    clearTimeout(restartTimer);
    if (rec) {
      const old = rec;
      old._ignore = true;
      rec = null;
      try { old.abort(); } catch (err) { /* noop */ }
    }
    if (recWanted) restartTimer = setTimeout(startRecognition, delay);
  }

  function abortRecognition() {
    recWanted = false;
    clearTimeout(restartTimer);
    if (rec) {
      const old = rec;
      old._ignore = true;
      rec = null;
      try { old.abort(); } catch (err) { /* noop */ }
    }
  }

  /* ---------- 음량 / VAD (AnalyserNode) ---------- */
  async function openAudio(token) {
    if (noAnalyser || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) return true;
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return true;
    let s = null;
    try {
      s = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
    } catch (err) {
      if (err && (err.name === 'NotAllowedError' || err.name === 'SecurityError')) return false;
      return true; // 마이크 장치 문제 등은 인식 쪽에서 다시 판단, 음량은 추정치로
    }
    if (token !== sessionToken) {
      s.getTracks().forEach((t) => t.stop());
      return true;
    }
    try {
      const ctx = new AC();
      if (ctx.state === 'suspended') await ctx.resume();
      const src = ctx.createMediaStreamSource(s);
      const an = ctx.createAnalyser();
      an.fftSize = 512;
      an.smoothingTimeConstant = 0.6;
      src.connect(an);
      stream = s;
      audioCtx = ctx;
      analyser = an;
      timeBuf = new Uint8Array(an.fftSize);
      freqBuf = new Uint8Array(an.frequencyBinCount);
    } catch (err) {
      s.getTracks().forEach((t) => t.stop());
    }
    return true;
  }

  function releaseAudio() {
    if (stream) stream.getTracks().forEach((t) => t.stop());
    if (audioCtx && audioCtx.close) { try { audioCtx.close(); } catch (err) { /* noop */ } }
    stream = null;
    audioCtx = null;
    analyser = null;
  }

  function tick(now) {
    rafId = requestAnimationFrame(tick);
    let target = 0;

    if (analyser) {
      analyser.getByteTimeDomainData(timeBuf);
      let sum = 0;
      for (let i = 0; i < timeBuf.length; i++) {
        const x = (timeBuf[i] - 128) / 128;
        sum += x * x;
      }
      const rms = Math.sqrt(sum / timeBuf.length);
      const threshold = Math.max(0.03, noiseFloor * 2.4);
      if (rms > threshold) markVoice();
      else noiseFloor = noiseFloor * 0.97 + rms * 0.03;
      target = Math.min(1, Math.max(0, (rms - noiseFloor) * 9));
      analyser.getByteFrequencyData(freqBuf);
    }

    const speaking = now - lastVoiceAt < SPEAK_HANGOVER_MS;
    if (state === 'listening' && speaking) setState('speaking');
    else if (state === 'speaking' && !speaking) setState('listening');

    if (!analyser) {
      // 음량 측정을 못 쓰는 환경: 인식 이벤트 기반 추정 파형
      target = speaking ? 0.35 + 0.3 * Math.abs(Math.sin(now / 140)) : 0;
    }
    level += (target - level) * 0.35;
    btn.style.setProperty('--level', level.toFixed(3));

    for (let k = 0; k < bars.length; k++) {
      let scale;
      if (state === 'speaking') {
        if (analyser) {
          const v = freqBuf[2 + k * 5] / 255;
          scale = 0.35 + Math.min(1.15, v * 1.3 + level * 0.5);
        } else {
          scale = 0.4 + level * 0.9 * (0.65 + 0.35 * Math.sin(now / 90 + k * 1.3));
        }
      } else {
        scale = 0.35 + 0.04 * Math.sin(now / 700 + k); // listening: 아주 약한 대기
      }
      bars[k].style.transform = 'scaleY(' + scale.toFixed(3) + ')';
    }
  }

  function stopLoop() {
    cancelAnimationFrame(rafId);
    rafId = 0;
  }

  /* ---------- 세션 시작 / 종료 ---------- */
  async function beginSession() {
    if (starting) return;
    if (!SR) {
      notice('이 브라우저는 음성 입력을 지원하지 않아요. 최신 Chrome, Edge 또는 Safari에서 이용해 주세요.');
      return;
    }
    starting = true;
    const token = ++sessionToken;
    clearTimeout(stopTimer);
    const ok = await openAudio(token);
    starting = false;
    if (token !== sessionToken) { releaseAudio(); return; }
    if (!ok) {
      setState('idle');
      resetBars(0.6);
      notice('마이크 권한이 필요해요. 브라우저 주소창의 권한 설정에서 마이크를 허용해 주세요.');
      return;
    }
    recWanted = true;
    rapidEnds = 0;
    noiseFloor = 0.01;
    lastVoiceAt = -1e9;
    setState('listening');
    startRecognition();
    stopLoop();
    rafId = requestAnimationFrame(tick);
  }

  // 사용자가 마이크를 다시 눌러 종료: stopping(빨간불) 3초 → idle
  function endSessionByUser() {
    sessionToken++;
    recWanted = false;
    clearTimeout(restartTimer);
    if (rec) { try { rec.stop(); } catch (err) { /* noop */ } } // 마지막 말까지 반영하고 종료
    releaseAudio();
    stopLoop();
    resetBars(0.6);
    setState('stopping');
    clearTimeout(stopTimer);
    stopTimer = setTimeout(() => setState('idle'), STOP_HOLD_MS);
  }

  // 폼 제출 / 페이지 이탈 / 오류: 즉시 idle
  function hardStop() {
    sessionToken++;
    starting = false;
    abortRecognition();
    releaseAudio();
    stopLoop();
    clearTimeout(stopTimer);
    resetBars(0.6);
    setState('idle');
  }

  function fatalStop(message) {
    hardStop();
    notice(message);
  }

  /* ---------- 이벤트 ---------- */
  btn.addEventListener('click', () => {
    if (state === 'idle') beginSession();
    else if (state === 'stopping') beginSession();
    else endSessionByUser();
  });

  // 입력창을 직접 지우거나 고쳐도 마이크 상태와 독립: 이후 음성은 현재 내용 뒤에 이어서 입력
  input.addEventListener('input', (event) => {
    if (!event.isTrusted || !recWanted) return;
    recycleRecognition(350);
  });

  if (memoryForm) memoryForm.addEventListener('submit', hardStop);
  window.addEventListener('pagehide', hardStop);
  document.addEventListener('visibilitychange', () => { if (document.hidden && recWanted) hardStop(); });

  btn.dataset.state = 'idle';
  cluster.dataset.state = 'idle';
  resetBars(0.6);
})();