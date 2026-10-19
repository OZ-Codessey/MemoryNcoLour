(() => {
  const trigger = document.getElementById('macChatTrigger');
  const panel = document.getElementById('macChatPanel');
  const closeButton = document.getElementById('macChatClose');
  const messages = document.getElementById('macChatMessages');
  const form = document.getElementById('macChatForm');
  const input = document.getElementById('macChatInput');
  const sendButton = document.getElementById('macChatSend');
  const statusLabel = document.getElementById('macChatStatus');
  const hexButton = document.getElementById('macChatHex'); // "추억의 HEX 찾기" 버튼
  const suggestionButtons = panel ? panel.querySelectorAll('[data-chat-question]') : [];
  if (!trigger || !panel || !closeButton || !messages || !form || !input || !sendButton) return;

  const GREETING = '안녕하세요. MaC 컨시어지입니다.\n기억을 색으로 표현하는 방법이나 서비스 이용에 대해 편하게 물어보세요.';
  const HEX_PROMPT = '추억의 HEX 찾기를 시작할게요.\n떠올리고 싶은 기억을 한두 문장으로 적어 주세요. (10자 이상)\n예: 할머니 댁 마루에 비치던 늦여름 오후의 햇살';
  const HEX_PLACEHOLDER = '기억을 한두 문장으로 적어 주세요';
  const HEX_PATTERN = /^#[0-9A-Fa-f]{6}$/;
  // 사용자가 해시(코드)를 직접 물어봤을 때만 보여주기 위한 패턴
  const HASH_REQUEST = /해시|hash|고유\s*(코드|번호)|표본\s*코드|암호\s*(코드|값)|MaC-?\s*코드/i;
  let status = 'offline';
  let connectTimer = null;
  let connectSeq = 0;   // 연결 시도 번호 (중간에 해제되면 이전 결과 무시)
  let convoSeq = 0;     // 대화 번호 (새 대화 시작 시 이전 응답 무시)
  let history = [];     // 서버로 보내는 최근 대화 [{role, content}]
  let pending = false;  // 응답 대기 중
  let hexMode = false;  // HEX 찾기 모드 (다음 입력을 기억 문장으로 처리)
  let lastHex = null;   // 가장 최근 HEX 결과의 팔레트 (해시 요청 시에만 사용)
  const defaultPlaceholder = input.getAttribute('placeholder') || '';

  function addMessage(text, isUser = false) {
    const item = document.createElement('div');
    item.className = 'mac-chat-message' + (isUser ? ' is-user' : '');
    item.textContent = text;
    messages.appendChild(item);
    messages.scrollTop = messages.scrollHeight;
    return item;
  }

  function addLine(text) {
    const item = document.createElement('div');
    item.className = 'mac-chat-line';
    item.textContent = text;
    messages.appendChild(item);
    messages.scrollTop = messages.scrollHeight;
  }

  function resetConversation() {
    convoSeq++;
    history = [];
    lastHex = null;
    setHexMode(false);
    messages.innerHTML = '';
    addMessage(GREETING);
  }

  /* ---- HEX 찾기 모드 ---- */
  function setHexMode(on) {
    hexMode = !!on;
    if (hexButton) {
      hexButton.setAttribute('aria-pressed', String(hexMode));
      hexButton.classList.toggle('is-active', hexMode);
    }
    input.setAttribute('placeholder', hexMode ? HEX_PLACEHOLDER : defaultPlaceholder);
  }

  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (_) {
      return false;
    }
  }

  // 분석 결과 카드: 4색 칩(클릭하면 HEX 복사) + 해시 + 요약
  function addHexCard(data) {
    const palette = Array.isArray(data.palette) ? data.palette : [];
    const card = document.createElement('div');
    card.className = 'mac-chat-hex-card';

    const strip = document.createElement('div');
    strip.className = 'mac-chat-hex-strip';
    palette.forEach((color) => {
      const hex = String(color.hex || '').toUpperCase();
      if (!HEX_PATTERN.test(hex)) return;
      const seg = document.createElement('span');
      seg.style.backgroundColor = hex;
      const w = Number(color.weight ?? color.area_ratio);
      seg.style.flexGrow = w > 0 ? String(w) : '1';
      strip.appendChild(seg);
    });
    card.appendChild(strip);

    const chips = document.createElement('div');
    chips.className = 'mac-chat-hex-chips';

    palette.forEach((color) => {
      const hex = String(color.hex || '').toUpperCase();
      if (!HEX_PATTERN.test(hex)) return;

      const chip = document.createElement('button');
      chip.type = 'button';
      chip.className = 'mac-chat-hex-chip';
      chip.setAttribute('aria-label', `${color.color_name || 'Color'} ${hex} 복사`);

      const swatch = document.createElement('span');
      swatch.className = 'mac-chat-hex-swatch';
      swatch.style.backgroundColor = hex;

      const name = document.createElement('span');
      name.className = 'mac-chat-hex-name';
      name.textContent = color.color_name || '';

      const code = document.createElement('span');
      code.className = 'mac-chat-hex-code';
      code.textContent = hex;

      chip.append(swatch, name, code);
      chip.addEventListener('click', async () => {
        const copied = await copyText(hex);
        code.textContent = copied ? '복사됨' : hex;
        setTimeout(() => { code.textContent = hex; }, 1200);
      });
      chips.appendChild(chip);
    });
    card.appendChild(chips);

    if (data.memory_summary) {
      const summary = document.createElement('div');
      summary.className = 'mac-chat-hex-summary';
      summary.textContent = data.memory_summary;
      card.appendChild(summary);
    }

    messages.appendChild(card);
    messages.scrollTop = messages.scrollHeight;
  }

  async function findHex(memory) {
    if (status !== 'online' || pending) return;
    const convo = convoSeq;
    pending = true;
    sendButton.disabled = true;
    if (hexButton) hexButton.disabled = true;
    addMessage(memory, true);
    const bubble = addMessage('기억의 색을 찾는 중…');
    let result = null;
    let errorText = '';
    try {
      const res = await fetch('/api/analyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ type: 'hex_finder', memory })
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok && data.status === 'success' && Array.isArray(data.palette) && data.palette.length) {
        result = data;
      } else {
        errorText = data.error || '색을 찾지 못했어요. 잠시 후 다시 시도해 주세요.';
      }
    } catch (_) {
      errorText = '서버와 통신할 수 없어요. 잠시 후 다시 시도해 주세요.';
    }
    pending = false;
    sendButton.disabled = status !== 'online';
    if (hexButton) hexButton.disabled = status !== 'online';
    if (convo !== convoSeq) return; // 그 사이 새 대화가 시작됨

    if (result) {
      bubble.textContent = '이 기억의 색은 이렇게 담겼어요.';
      addHexCard(result);
      lastHex = { palette: result.palette };
      setHexMode(false); // 성공하면 모드 종료 (다시 누르면 새로 시작)
    } else {
      bubble.textContent = errorText;
      // 실패 시 모드를 유지해서 문장만 고쳐 다시 보낼 수 있게 함
    }
    messages.scrollTop = messages.scrollHeight;
  }

  // 사용자가 해시(코드)를 물어봤을 때만 서버에서 계산해 알려줌
  async function requestHash(question) {
    if (status !== 'online' || pending) return;
    const convo = convoSeq;
    addMessage(question, true);
    if (!lastHex) {
      addMessage('아직 찾은 HEX가 없어요. 먼저 \'추억의 HEX 찾기\'로 기억의 색을 찾아 주세요.');
      return;
    }
    pending = true;
    sendButton.disabled = true;
    if (hexButton) hexButton.disabled = true;
    const bubble = addMessage('…');
    let hash = '';
    try {
      const res = await fetch('/api/analyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ type: 'hex_hash', palette: lastHex.palette })
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok && data.status === 'success' && data.hash) hash = data.hash;
    } catch (_) { /* 아래에서 안내 */ }
    pending = false;
    sendButton.disabled = status !== 'online';
    if (hexButton) hexButton.disabled = status !== 'online';
    if (convo !== convoSeq) return;
    bubble.textContent = hash
      ? '이 기억의 고유 코드는 ' + hash + ' 예요.'
      : '코드를 가져오지 못했어요. 잠시 후 다시 시도해 주세요.';
    messages.scrollTop = messages.scrollHeight;
  }

  function toggleHexMode() {
    if (status !== 'online' || pending) return;
    if (hexMode) {
      setHexMode(false);
      addLine('HEX 찾기를 취소했어요.');
      return;
    }
    setHexMode(true);
    addMessage(HEX_PROMPT);
    input.focus();
  }

  /* ---- 온라인/오프라인 상태: 안테나 초록불 + 입력 가능 여부 ---- */
  function setStatus(next) {
    status = next === 'online' ? 'online' : 'offline';
    const on = status === 'online';
    trigger.classList.toggle('is-online', on);
    panel.classList.toggle('is-online', on);
    panel.dataset.status = status;
    if (statusLabel) statusLabel.textContent = on ? 'Online' : 'Offline';
    trigger.setAttribute('aria-label', on ? 'MaC 챗봇 닫기 (현재 온라인)' : 'MaC 챗봇 열기 (현재 오프라인)');
    input.disabled = !on;
    sendButton.disabled = !on;
    suggestionButtons.forEach((b) => { b.disabled = !on; });
    if (hexButton) hexButton.disabled = !on;
    if (!on) setHexMode(false);
  }

  // 서버 상태 확인(GET /api/chat) 후 성공하면 online. 반환: true=성공, false=실패, null=중간에 취소됨
  async function connect() {
    const seq = ++connectSeq;
    clearTimeout(connectTimer);
    if (statusLabel) statusLabel.textContent = 'Connecting…';
    const ctrl = new AbortController();
    connectTimer = setTimeout(() => ctrl.abort(), 8000);
    let ok = false;
    try {
      const res = await fetch('/api/chat', { method: 'GET', signal: ctrl.signal });
      const data = await res.json().catch(() => ({}));
      ok = res.ok && data.configured === true;
    } catch (_) {
      ok = false;
    }
    clearTimeout(connectTimer);
    if (seq !== connectSeq) return null;
    setStatus(ok ? 'online' : 'offline');
    if (ok && panel.classList.contains('is-open')) input.focus();
    return ok;
  }

  function disconnect() {
    connectSeq++;
    clearTimeout(connectTimer);
    setStatus('offline');
  }

  function openChat() {
    panel.classList.add('is-open');
    panel.setAttribute('aria-hidden', 'false');
    trigger.setAttribute('aria-expanded', 'true');
    if (!messages.children.length) resetConversation();
    connect().then((ok) => {   // 챗봇 버튼을 누르면 연결 → 안테나에 초록불
      if (ok === false) addLine('서버에 연결하지 못했어요. 잠시 후 다시 시도해 주세요.');
    });
  }

  function hidePanel() {
    if (panel.contains(document.activeElement)) trigger.focus({ preventScroll: true });
    panel.classList.remove('is-open');
    panel.setAttribute('aria-hidden', 'true');
    trigger.setAttribute('aria-expanded', 'false');
    disconnect(); // 닫으면 오프라인 → 안테나 소등
  }

  function closeChat() {
    hidePanel();
    trigger.focus();
  }

  async function send(question) {
    if (status !== 'online' || pending) return;
    const convo = convoSeq;
    pending = true;
    sendButton.disabled = true;
    if (hexButton) hexButton.disabled = true;
    addMessage(question, true);
    const bubble = addMessage('…');
    let reply = '';
    let errorText = '';
    try {
      const res = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: question, history: history.slice(-10) })
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok && data.reply) reply = data.reply;
      else errorText = data.error || '답변을 받지 못했어요. 잠시 후 다시 시도해 주세요.';
    } catch (_) {
      errorText = '서버와 통신할 수 없어요. 잠시 후 다시 시도해 주세요.';
    }
    pending = false;
    sendButton.disabled = status !== 'online';
    if (hexButton) hexButton.disabled = status !== 'online';
    if (convo !== convoSeq) return; // 그 사이 새 대화가 시작됨
    bubble.textContent = reply || errorText;
    messages.scrollTop = messages.scrollHeight;
    if (reply) history.push({ role: 'user', content: question }, { role: 'assistant', content: reply });
  }

  // 입력 전송 분기: HEX 모드면 색 분석, 해시 요청이면 코드 안내, 그 외는 일반 챗봇
  function submitText(text) {
    if (hexMode) findHex(text);
    else if (HASH_REQUEST.test(text)) requestHash(text);
    else send(text);
  }

  trigger.setAttribute('aria-expanded', 'false');
  setStatus('offline');

  trigger.addEventListener('click', () => {
    if (panel.classList.contains('is-open')) closeChat(); else openChat();
  });
  closeButton.addEventListener('click', closeChat);

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && panel.classList.contains('is-open')) closeChat();
  });
  document.addEventListener('click', (event) => {
    if (panel.classList.contains('is-open') && !panel.contains(event.target) && !trigger.contains(event.target)) {
      hidePanel();
    }
  });

  form.addEventListener('submit', (event) => {
    event.preventDefault();
    const question = input.value.trim();
    if (!question) return;
    input.value = '';
    submitText(question);
  });

  // Enter = 전송, Shift+Enter = 줄바꿈
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      form.requestSubmit();
    }
  });

  if (hexButton) hexButton.addEventListener('click', toggleHexMode);

  suggestionButtons.forEach((button) => {
    button.addEventListener('click', () => {
      if (hexMode) {          // 추천 질문을 누르면 HEX 모드는 자동 종료
        setHexMode(false);
      }
      send(button.getAttribute('data-chat-question'));
      input.focus();
    });
  });

  // 외부(백엔드 연동 코드)에서 상태를 제어할 수 있는 훅
  window.MaCChat = { open: openChat, close: closeChat, setStatus };
})();