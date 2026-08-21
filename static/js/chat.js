document.addEventListener('DOMContentLoaded', function () {
  const messagesArea = document.getElementById('messages-area');
  const welcomeState = document.getElementById('welcome-state');
  const msgInput = document.getElementById('msg-input');
  const sendBtn = document.getElementById('send-btn');
  const agentPickerEl = document.getElementById('agent-picker');
  const agentBadgeEl = document.getElementById('agent-badge');
  const attachBtn = document.getElementById('attach-btn');
  const fileInput = document.getElementById('file-input');
  const attachMenu = document.getElementById('attach-menu');
  const batchFilesInput = document.getElementById('batch-files-input');
  const batchFolderInput = document.getElementById('batch-folder-input');
  const kbBtn = document.getElementById('kb-btn');
  const kbBadge = document.getElementById('kb-badge');
  const kbBackdrop = document.getElementById('kb-modal-backdrop');
  const kbListEl = document.getElementById('kb-list');
  const kbSearchEl = document.getElementById('kb-search');
  const kbCountEl = document.getElementById('kb-count');
  const kbCloseBtn = document.getElementById('kb-modal-close');
  const kbCancelBtn = document.getElementById('kb-cancel-btn');
  const kbSaveBtn = document.getElementById('kb-save-btn');
  const kbClearBtn = document.getElementById('kb-clear-btn');
  const knowBtn = document.getElementById('know-btn');
  const knowBadge = document.getElementById('know-badge');
  const knowBackdrop = document.getElementById('know-modal-backdrop');
  const knowListEl = document.getElementById('know-list');
  const knowSearchEl = document.getElementById('know-search');
  const knowCountEl = document.getElementById('know-count');
  const knowWarnEl = document.getElementById('know-multi-warn');
  const knowCloseBtn = document.getElementById('know-modal-close');
  const knowCancelBtn = document.getElementById('know-cancel-btn');
  const knowSaveBtn = document.getElementById('know-save-btn');
  const knowClearBtn = document.getElementById('know-clear-btn');

  let isLoading = false;
  let conversationId = null;
  let selectedAgentSlug = null;
  let agentsCache = [];
  let hasSessionAgent = false;
  let streamController = null;   // AbortController do turno em andamento
  let stopRequested = false;     // usuário clicou em parar neste turno
  let stopFallbackTimer = null;  // aborta o fetch se o backend não encerrar a tempo
  let activeKbs = [];            // KBs ativas nesta conversa [{id,name,description}]
  let kbCatalog = null;          // catálogo carregado de /api/kbs/
  let kbDraft = new Set();       // ids marcados no modal antes de salvar
  let activeKnowledge = [];      // conhecimentos ativos [{id}]
  let knowCatalog = null;        // catálogo carregado de /api/knowledge/
  let knowDraft = new Set();     // ids marcados no modal antes de salvar

  marked.setOptions({
    breaks: true,
    gfm: true,
    highlight(code, lang) {
      if (lang && hljs.getLanguage(lang)) return hljs.highlight(code, { language: lang }).value;
      return hljs.highlightAuto(code).value;
    },
  });

  msgInput.addEventListener('input', () => {
    msgInput.style.height = 'auto';
    msgInput.style.height = Math.min(msgInput.scrollHeight, 200) + 'px';
    updateSendBtn();
  });

  msgInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      if (!sendBtn.disabled) sendMessage();
    }
  });

  sendBtn.addEventListener('click', () => {
    // Em modo "parar" o mesmo botão interrompe a geração em andamento.
    if (isLoading) { stopGeneration(); return; }
    sendMessage();
  });

  // Ícones SVG do botão: enviar (seta) e parar (quadrado).
  const SEND_ICON = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5M5 12l7-7 7 7"/></svg>';
  const STOP_ICON = '<svg width="14" height="14" viewBox="0 0 24 24" fill="#fff" stroke="none"><rect x="5" y="5" width="14" height="14" rx="2.5"/></svg>';

  // Alterna o botão entre os modos enviar/parar.
  function setSendBtnMode(stopMode) {
    if (stopMode) {
      sendBtn.classList.add('is-stop');
      sendBtn.innerHTML = STOP_ICON;
      sendBtn.title = 'Parar geração';
      sendBtn.disabled = false;
    } else {
      sendBtn.classList.remove('is-stop');
      sendBtn.innerHTML = SEND_ICON;
      sendBtn.title = 'Enviar (Enter)';
      updateSendBtn();
    }
  }

  // Interrompe o turno atual. Estratégia híbrida: avisa o backend (que para no
  // próximo passo e entrega o parcial pelo próprio stream) e, como fallback,
  // aborta o fetch caso ele não encerre logo (ex.: preso num passo longo).
  async function stopGeneration() {
    if (!isLoading || stopRequested) return;
    stopRequested = true;
    sendBtn.disabled = true;
    sendBtn.title = 'Parando…';

    let notified = false;
    if (conversationId) {
      try {
        await fetch(`/api/conversations/${conversationId}/stop/`, {
          method: 'POST',
          headers: { 'X-CSRFToken': getCookie('csrftoken') },
        });
        notified = true;
      } catch (e) { /* cai no abort abaixo */ }
    }

    if (notified) {
      // Dá uma janela para o backend fechar o turno com o parcial salvo;
      // se não vier, aborta o stream local.
      stopFallbackTimer = setTimeout(() => {
        if (streamController) streamController.abort();
      }, 6000);
    } else {
      // Sem conversa ainda (1ª msg) ou falha ao avisar: aborta direto.
      if (streamController) streamController.abort();
    }
  }

  // ── Menu de anexo (um arquivo · vários PDFs/TXTs · pasta) ──────────
  function closeAttachMenu() {
    if (attachMenu) attachMenu.hidden = true;
  }

  if (attachBtn && attachMenu) {
    attachBtn.addEventListener('click', (e) => {
      if (isLoading) return;
      e.stopPropagation();
      attachMenu.hidden = !attachMenu.hidden;
    });

    attachMenu.querySelectorAll('.attach-menu-item').forEach((item) => {
      item.addEventListener('click', () => {
        const kind = item.dataset.attach;
        closeAttachMenu();
        if (isLoading) return;
        if (kind === 'single') fileInput.click();
        else if (kind === 'files') batchFilesInput.click();
        else if (kind === 'folder') batchFolderInput.click();
      });
    });

    // Fecha ao clicar fora ou apertar Esc.
    document.addEventListener('click', (e) => {
      if (attachMenu.hidden) return;
      if (!attachMenu.contains(e.target) && e.target !== attachBtn && !attachBtn.contains(e.target)) {
        closeAttachMenu();
      }
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') closeAttachMenu();
    });
  }

  if (fileInput) {
    fileInput.addEventListener('change', () => {
      const files = Array.from(fileInput.files || []);
      fileInput.value = '';
      if (files.length) uploadTablesSequentially(files);
    });
  }

  // Sobe vários arquivos "avulsos" um de cada vez. Sequencial de propósito:
  // o backend cria a conversa na 1ª chamada (sem conversation_id) e o
  // uploadTable() atualiza `conversationId`; disparar em paralelo criaria
  // várias conversas. Cada tabela nova preserva a anterior em named_datasets.
  async function uploadTablesSequentially(files) {
    for (const file of files) {
      await uploadTable(file);
    }
  }

  // Aceita só PDF/TXT (a pasta pode trazer qualquer coisa dentro).
  function keepPdfTxt(fileList) {
    return Array.from(fileList || []).filter((f) => /\.(pdf|txt)$/i.test(f.name));
  }

  if (batchFilesInput) {
    batchFilesInput.addEventListener('change', () => {
      const files = keepPdfTxt(batchFilesInput.files);
      if (files.length) uploadBatchDocs(files);
      else if (batchFilesInput.files.length) {
        appendMessage({ role: 'assistant', content: '❌ Nenhum arquivo suportado. Selecione PDFs ou TXTs.', tool_calls: [] });
      }
      batchFilesInput.value = '';
    });
  }

  if (batchFolderInput) {
    batchFolderInput.addEventListener('change', () => {
      const files = keepPdfTxt(batchFolderInput.files);
      if (files.length) uploadBatchDocs(files);
      else if (batchFolderInput.files.length) {
        appendMessage({ role: 'assistant', content: '❌ A pasta não tem nenhum PDF ou TXT.', tool_calls: [] });
      }
      batchFolderInput.value = '';
    });
  }

  function updateSendBtn() {
    // Em modo "parar" (geração em curso) o botão fica sempre ativo.
    if (isLoading) { sendBtn.disabled = stopRequested; return; }
    sendBtn.disabled = msgInput.value.trim() === '';
  }

  function fillSuggestion(text) {
    msgInput.value = text;
    msgInput.style.height = 'auto';
    msgInput.style.height = Math.min(msgInput.scrollHeight, 200) + 'px';
    updateSendBtn();
    msgInput.focus();
  }
  window.fillSuggestion = fillSuggestion;

  function getCookie(name) {
    let val = null;
    if (document.cookie) {
      for (const c of document.cookie.split(';')) {
        const ck = c.trim();
        if (ck.startsWith(name + '=')) { val = decodeURIComponent(ck.substring(name.length + 1)); break; }
      }
    }
    return val;
  }

  // Renderiza markdown em um elemento e (1) envolve cada <table> num
  // .table-scroll para rolagem horizontal (evita vazar do balão) e (2)
  // destaca blocos de código. Centraliza o que antes era repetido em vários
  // pontos — e onde as tabelas não eram envolvidas, causando overflow.
  function renderMarkdownInto(el, text) {
    el.innerHTML = marked.parse(text || '');
    el.querySelectorAll('table').forEach(table => {
      // Não re-envolver se já estiver dentro de um scroller.
      if (table.parentElement && table.parentElement.classList.contains('table-scroll')) return;
      const scroller = document.createElement('div');
      scroller.className = 'table-scroll';
      table.parentNode.insertBefore(scroller, table);
      scroller.appendChild(table);
    });
    el.querySelectorAll('pre code').forEach(code => hljs.highlightElement(code));
  }

  // ── Agentes ────────────────────────────────────────────────
  async function loadAgents() {
    try {
      const res = await fetch('/api/config/');
      const data = await res.json();
      agentsCache = data.agents || [];
      const defaultAgent = agentsCache.find(a => a.is_default) || agentsCache[0];
      if (!selectedAgentSlug && defaultAgent) selectedAgentSlug = defaultAgent.slug;
      renderAgentPicker();
    } catch (e) { /* silencioso */ }
  }

  function renderAgentPicker() {
    if (!agentPickerEl) return;
    agentPickerEl.innerHTML = '';
    agentsCache.forEach(a => {
      const opt = document.createElement('option');
      opt.value = a.slug;
      opt.textContent = `${a.icon} ${a.name}`;
      if (a.slug === selectedAgentSlug) opt.selected = true;
      agentPickerEl.appendChild(opt);
    });
    updateAgentBadge();
  }

  function updateAgentBadge() {
    if (!agentBadgeEl) return;
    const a = agentsCache.find(x => x.slug === selectedAgentSlug);
    if (a) {
      agentBadgeEl.innerHTML = `<span class="dot"></span><span>${a.icon} ${a.name}</span>`;
    }
  }

  if (agentPickerEl) {
    agentPickerEl.addEventListener('change', (e) => {
      selectedAgentSlug = e.target.value;
      updateAgentBadge();
    });
  }

  // ── Conversa existente ─────────────────────────────────────
  async function loadExistingConversation() {
    const params = new URLSearchParams(window.location.search);
    const id = params.get('c');
    if (!id) return;
    try {
      const res = await fetch(`/api/conversations/${id}/`);
      if (!res.ok) return;
      const data = await res.json();
      conversationId = data.id;
      if (data.agent_slug) {
        selectedAgentSlug = data.agent_slug;
        renderAgentPicker();
      }
      hasSessionAgent = !!data.has_session_agent;
      updateSessionAgentBtn();
      if (welcomeState && welcomeState.parentNode) welcomeState.remove();
      (data.messages || []).forEach(m => appendMessage(m));
      if (data.awaiting_human_input) showAwaitingHumanIndicator();
      loadConversationKbs();
      loadConversationKnowledge();
    } catch (e) { /* silencioso */ }
  }

  function showAwaitingHumanIndicator() {
    msgInput.placeholder = '✋ O agente está aguardando sua resposta…';
    msgInput.focus();
  }

  // ── Envio de mensagem ──────────────────────────────────────
  async function sendMessage() {
    const text = msgInput.value.trim();
    if (!text || isLoading) return;

    if (welcomeState && welcomeState.parentNode) welcomeState.remove();

    isLoading = true;
    stopRequested = false;
    streamController = new AbortController();
    setSendBtnMode(true);
    msgInput.value = '';
    msgInput.style.height = 'auto';
    msgInput.placeholder = 'Descreva o que deseja auditar…';

    appendMessage({ role: 'user', content: text, tool_calls: [] });
    const typingEl = appendTyping();

    try {
      const response = await fetch('/api/chat/stream/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
        body: JSON.stringify({
          message: text,
          conversation_id: conversationId,
          agent_slug: selectedAgentSlug,
          active_kbs: activeKbs,
          active_knowledge: activeKnowledge,
        }),
        signal: streamController.signal,
      });

      const data = await consumeStream(response, (evt) => updateTypingProgress(typingEl, evt));
      typingEl.remove();

      if (data && data.status === 'success') {
        const reply = data.reply || {};
        appendMessage({
          role: 'assistant',
          content: reply.content || '',
          tool_calls: reply.tool_calls || [],
          attachment: reply.attachment || null,
          attachments: reply.attachments || [],
        });
        const isNew = !conversationId;
        conversationId = data.conversation_id;
        if (isNew && conversationId) {
          history.replaceState(null, '', '/?c=' + conversationId);
        }
        if (data.awaiting_human_input) showAwaitingHumanIndicator();
        if (window.refreshHistory) window.refreshHistory();
      } else {
        const msg = (data && data.message) || 'Ocorreu um erro.';
        appendMessage({ role: 'assistant', content: '❌ Erro: ' + msg, tool_calls: [] });
      }
    } catch (err) {
      typingEl.remove();
      if (err && err.name === 'AbortError') {
        // Stop local: o backend foi avisado e mantém o parcial. Recarrega a
        // conversa para exibir a resposta parcial já persistida no servidor.
        appendMessage({ role: 'assistant', content: '⏹️ Geração interrompida.', tool_calls: [] });
        if (window.refreshHistory) window.refreshHistory();
      } else {
        appendMessage({ role: 'assistant', content: '❌ Falha na conexão: ' + err.message, tool_calls: [] });
      }
    }

    isLoading = false;
    stopRequested = false;
    streamController = null;
    if (stopFallbackTimer) { clearTimeout(stopFallbackTimer); stopFallbackTimer = null; }
    setSendBtnMode(false);
    msgInput.focus();
  }

  // ── Consome o stream SSE: chama onProgress(evt) a cada evento de progresso
  //    e retorna o payload final (evento 'done') ou {status:'error'}. ────────
  async function consumeStream(response, onProgress) {
    if (!response.ok || !response.body) {
      // Fallback: tenta ler como JSON (ex.: erro 400 do backend).
      try { return await response.json(); }
      catch { return { status: 'error', message: 'HTTP ' + response.status }; }
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let finalPayload = null;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // Frames SSE separados por linha em branco.
      let sep;
      while ((sep = buffer.indexOf('\n\n')) !== -1) {
        const frame = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);
        // Ignora comentários (heartbeat ': ...') e linhas sem 'data:'.
        const line = frame.split('\n').find(l => l.startsWith('data:'));
        if (!line) continue;
        let evt;
        try { evt = JSON.parse(line.slice(5).trim()); }
        catch { continue; }

        if (evt.type === 'progress') {
          onProgress(evt);
        } else if (evt.type === 'done') {
          finalPayload = evt.payload;
        } else if (evt.type === 'error') {
          finalPayload = { status: 'error', message: evt.message };
        }
      }
    }
    return finalPayload;
  }

  // ── Upload de tabela ───────────────────────────────────────
  async function uploadTable(file) {
    if (isLoading) return;
    if (welcomeState && welcomeState.parentNode) welcomeState.remove();

    isLoading = true;
    updateSendBtn();
    if (attachBtn) attachBtn.disabled = true;

    const note = msgInput.value.trim();
    msgInput.value = '';
    msgInput.style.height = 'auto';

    const placeholder = appendUploadingPlaceholder(file.name);

    try {
      const fd = new FormData();
      fd.append('file', file);
      if (conversationId) fd.append('conversation_id', conversationId);
      if (selectedAgentSlug) fd.append('agent_slug', selectedAgentSlug);
      if (note) fd.append('note', note);

      const res = await fetch('/api/upload/', {
        method: 'POST',
        headers: { 'X-CSRFToken': getCookie('csrftoken') },
        body: fd,
      });
      const text = await res.text();
      let data;
      try {
        data = JSON.parse(text);
      } catch {
        // Servidor devolveu HTML (página de debug do Django) — extrai algo útil
        const snippet = text.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 300);
        data = { status: 'error', message: `HTTP ${res.status} (resposta não-JSON): ${snippet || 'sem detalhe'}` };
      }
      placeholder.remove();

      if (data.status === 'success') {
        const isNew = !conversationId;
        conversationId = data.conversation_id;
        if (isNew && conversationId) {
          history.replaceState(null, '', '/?c=' + conversationId);
        }
        appendMessage(data.message);
        if (window.refreshHistory) window.refreshHistory();
      } else {
        appendMessage({ role: 'assistant', content: '❌ Falha no upload: ' + (data.message || 'erro desconhecido'), tool_calls: [] });
      }
    } catch (err) {
      placeholder.remove();
      appendMessage({ role: 'assistant', content: '❌ Falha no upload: ' + err.message, tool_calls: [] });
    }

    isLoading = false;
    if (attachBtn) attachBtn.disabled = false;
    updateSendBtn();
  }

  // ── Upload em lote (vários PDFs/TXTs ou pasta inteira) ──────
  const BATCH_MAX_FILES = 200;

  async function uploadBatchDocs(files) {
    if (isLoading) return;
    if (files.length > BATCH_MAX_FILES) {
      appendMessage({ role: 'assistant', content: `❌ Máximo de ${BATCH_MAX_FILES} arquivos por vez (você selecionou ${files.length}).`, tool_calls: [] });
      return;
    }
    if (welcomeState && welcomeState.parentNode) welcomeState.remove();

    isLoading = true;
    updateSendBtn();
    if (attachBtn) attachBtn.disabled = true;

    const note = msgInput.value.trim();
    msgInput.value = '';
    msgInput.style.height = 'auto';

    const label = files.length === 1 ? files[0].name : `${files.length} documentos`;
    const placeholder = appendUploadingPlaceholder(label);
    const placeholderText = placeholder.querySelector('.upload-placeholder span:last-child');

    try {
      const fd = new FormData();
      files.forEach((f) => fd.append('files', f));
      if (conversationId) fd.append('conversation_id', conversationId);
      if (selectedAgentSlug) fd.append('agent_slug', selectedAgentSlug);
      if (note) fd.append('note', note);

      const res = await fetch('/api/upload-batch/', {
        method: 'POST',
        headers: { 'X-CSRFToken': getCookie('csrftoken') },
        body: fd,
      });

      const data = await consumeStream(res, (evt) => {
        if (placeholderText && evt.total) {
          const nome = evt.filename ? ` <strong>${escapeHtml(evt.filename)}</strong>` : '';
          placeholderText.innerHTML = `Extraindo ${evt.done} de ${evt.total}${nome}…`;
        }
      });
      placeholder.remove();

      if (data && data.status === 'success') {
        const isNew = !conversationId;
        conversationId = data.conversation_id;
        if (isNew && conversationId) {
          history.replaceState(null, '', '/?c=' + conversationId);
        }
        appendMessage(data.message);
        if (window.refreshHistory) window.refreshHistory();
      } else {
        appendMessage({ role: 'assistant', content: '❌ Falha no upload: ' + ((data && data.message) || 'erro desconhecido'), tool_calls: [] });
      }
    } catch (err) {
      placeholder.remove();
      appendMessage({ role: 'assistant', content: '❌ Falha no upload: ' + err.message, tool_calls: [] });
    }

    isLoading = false;
    if (attachBtn) attachBtn.disabled = false;
    updateSendBtn();
  }

  function appendUploadingPlaceholder(filename) {
    const row = document.createElement('div');
    row.className = 'message-row user-row';
    row.innerHTML = `
      <div class="msg-avatar user">V</div>
      <div class="msg-content-wrap">
        <div class="msg-author">Você</div>
        <div class="upload-placeholder">
          <span class="spinner"></span>
          <span>Carregando <strong>${escapeHtml(filename)}</strong>…</span>
        </div>
      </div>`;
    messagesArea.appendChild(row);
    scrollToBottom();
    return row;
  }

  // Renderiza um card de anexo (de qualquer kind) dentro de `wrap`.
  // Centraliza o dispatch para que tanto o anexo único (`attachment`)
  // quanto a lista de artefatos do turno (`attachments`) usem o mesmo caminho.
  function appendAttachmentCard(wrap, att) {
    if (!att || !att.kind) return;
    if (att.kind === 'table') {
      wrap.appendChild(renderTableCard(att));
    } else if (att.kind === 'document') {
      wrap.appendChild(renderDocumentCard(att));
    } else if (att.kind === 'export') {
      const holder = document.createElement('div');
      holder.innerHTML = renderExportCard(att);
      const cardNode = holder.firstElementChild;
      if (cardNode) wrap.appendChild(cardNode);
    } else if (att.kind === 'mermaid') {
      wrap.appendChild(renderMermaidCard(att));
    } else if (att.kind === 'chart') {
      wrap.appendChild(renderChartCard(att));
    }
  }

  // ── Renderização de mensagens ─────────────────────────────
  function appendMessage(m) {
    const role = m.role;
    const content = m.content || '';
    const toolCalls = m.tool_calls || [];
    const attachment = m.attachment || null;
    const isUser = role === 'user';

    const row = document.createElement('div');
    row.className = `message-row ${isUser ? 'user-row' : 'assistant-row'}`;

    const avatar = document.createElement('div');
    avatar.className = `msg-avatar ${role}`;
    avatar.textContent = isUser ? 'V' : '🛡️';

    const wrap = document.createElement('div');
    wrap.className = 'msg-content-wrap';

    const author = document.createElement('div');
    author.className = 'msg-author';
    author.textContent = isUser ? 'Você' : 'Multi-Agentes Auditoria';
    wrap.appendChild(author);

    if (toolCalls.length > 0) {
      const group = document.createElement('div');
      group.className = 'tool-calls-group';
      toolCalls.forEach(tc => group.appendChild(renderToolCall(tc)));
      wrap.appendChild(group);
    }

    // Anexo único (ex.: tabela/documento de upload do usuário).
    appendAttachmentCard(wrap, attachment);
    // Cards de artefato do turno (vários por mensagem: PDF + Excel,
    // dois gráficos, etc). Cada um vira um card próprio.
    (m.attachments || []).forEach(att => appendAttachmentCard(wrap, att));

    if (content) {
      const msgText = document.createElement('div');
      msgText.className = 'msg-text';
      if (isUser) {
        // Usuário com tabela: o content é o resumo técnico — mostrar discreto.
        if (attachment) {
          const det = document.createElement('details');
          det.className = 'attachment-summary';
          const sum = document.createElement('summary');
          sum.textContent = 'Mostrar resumo enviado ao agente';
          det.appendChild(sum);
          const body = document.createElement('div');
          body.className = 'attachment-summary-body';
          renderMarkdownInto(body, content);
          det.appendChild(body);
          msgText.appendChild(det);
        } else {
          msgText.textContent = content;
        }
      } else {
        renderMarkdownInto(msgText, content);
      }
      wrap.appendChild(msgText);
    }

    row.appendChild(avatar);
    row.appendChild(wrap);
    messagesArea.appendChild(row);
    scrollToBottom();
    return row;
  }

  // ── Card-tabela com mini/expandir/paginação ────────────────
  function renderTableCard(att) {
    const card = document.createElement('div');
    card.className = 'table-card';

    const columns = att.columns || [];
    const dtypes = att.dtypes || {};
    const previewRows = att.preview || [];
    const total = att.rows || 0;

    const state = {
      expanded: false,
      offset: 0,
      limit: 100,
      rows: previewRows.slice(),
      total,
      origParent: null,
      origNext: null,
      backdrop: null,
    };

    card.innerHTML = `
      <div class="table-card-header">
        <span class="table-card-icon">📊</span>
        <div class="table-card-meta">
          <div class="table-card-title">${escapeHtml(att.filename || 'tabela')}</div>
          <div class="table-card-sub">${total.toLocaleString('pt-BR')} linhas · ${columns.length} colunas${att.truncated ? ' · truncado' : ''}</div>
        </div>
        <button class="table-card-btn" data-action="expand" title="Expandir tabela">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="15 3 21 3 21 9"/><polyline points="9 21 3 21 3 15"/>
            <line x1="21" y1="3" x2="14" y2="10"/><line x1="3" y1="21" x2="10" y2="14"/>
          </svg>
          <span>Expandir</span>
        </button>
      </div>
      <div class="table-card-viewport">
        <table class="table-grid"></table>
      </div>
      <div class="table-card-footer">
        <span class="table-pager-info"></span>
        <div class="table-pager-actions">
          <button class="table-pager-btn" data-action="prev">← Anterior</button>
          <button class="table-pager-btn" data-action="next">Próximo →</button>
        </div>
      </div>
    `;

    const grid = card.querySelector('.table-grid');
    const pagerInfo = card.querySelector('.table-pager-info');
    const prevBtn = card.querySelector('[data-action="prev"]');
    const nextBtn = card.querySelector('[data-action="next"]');
    const expandBtn = card.querySelector('[data-action="expand"]');

    function render() {
      // header
      let html = '<thead><tr>';
      columns.forEach(c => {
        const t = dtypes[c] || '';
        html += `<th><span class="th-name">${escapeHtml(c)}</span>${t ? `<span class="th-type">${escapeHtml(t)}</span>` : ''}</th>`;
      });
      html += '</tr></thead><tbody>';
      const visible = state.expanded ? state.rows : state.rows.slice(0, 5);
      if (!visible.length) {
        html += `<tr><td colspan="${columns.length || 1}" class="td-empty">sem dados</td></tr>`;
      } else {
        visible.forEach(r => {
          html += '<tr>';
          columns.forEach(c => {
            const v = r[c];
            html += `<td>${v === null || v === undefined ? '<span class="td-null">∅</span>' : escapeHtml(String(v))}</td>`;
          });
          html += '</tr>';
        });
      }
      html += '</tbody>';
      grid.innerHTML = html;

      // pager
      if (state.expanded) {
        const start = state.offset + 1;
        const end = state.offset + state.rows.length;
        pagerInfo.textContent = `${start.toLocaleString('pt-BR')}–${end.toLocaleString('pt-BR')} de ${state.total.toLocaleString('pt-BR')}`;
        prevBtn.disabled = state.offset <= 0;
        nextBtn.disabled = state.offset + state.limit >= state.total;
      } else {
        pagerInfo.textContent = `Pré-visualizando 5 de ${state.total.toLocaleString('pt-BR')} linhas`;
        prevBtn.disabled = true;
        nextBtn.disabled = true;
      }
    }

    async function fetchPage(offset) {
      if (!conversationId) return;
      try {
        const res = await fetch(`/api/conversations/${conversationId}/dataset/?offset=${offset}&limit=${state.limit}`);
        const data = await res.json();
        state.rows = data.rows || [];
        state.offset = data.offset || 0;
        state.total = data.total || state.total;
        render();
      } catch (e) { /* silencioso */ }
    }

    function expand() {
      state.expanded = true;
      // Salva posição original e move para body (escapa o stacking context do .main)
      state.origParent = card.parentNode;
      state.origNext = card.nextSibling;
      // Backdrop em elemento próprio (clicar fecha)
      const backdrop = document.createElement('div');
      backdrop.className = 'table-card-backdrop';
      backdrop.addEventListener('click', minimize);
      document.body.appendChild(backdrop);
      document.body.appendChild(card);
      state.backdrop = backdrop;
      card.classList.add('expanded');
      document.body.classList.add('modal-open');
      expandBtn.querySelector('span').textContent = 'Minimizar';
      document.addEventListener('keydown', onKey);
      fetchPage(0);
    }

    function minimize() {
      if (!state.expanded) return;
      state.expanded = false;
      card.classList.remove('expanded');
      document.body.classList.remove('modal-open');
      expandBtn.querySelector('span').textContent = 'Expandir';
      document.removeEventListener('keydown', onKey);
      if (state.backdrop) {
        state.backdrop.remove();
        state.backdrop = null;
      }
      // Devolve para o lugar original na timeline do chat
      if (state.origParent) {
        state.origParent.insertBefore(card, state.origNext);
      }
      state.rows = previewRows.slice();
      state.offset = 0;
      render();
    }

    function onKey(e) {
      if (e.key === 'Escape') minimize();
    }

    expandBtn.addEventListener('click', () => {
      if (state.expanded) minimize();
      else expand();
    });

    prevBtn.addEventListener('click', () => {
      if (state.offset <= 0) return;
      fetchPage(Math.max(0, state.offset - state.limit));
    });
    nextBtn.addEventListener('click', () => {
      if (state.offset + state.limit >= state.total) return;
      fetchPage(state.offset + state.limit);
    });

    render();
    return card;
  }

  // ── Card-documento (PDF/DOCX/imagem extraído via docling) ──
  function renderDocumentCard(att) {
    const card = document.createElement('div');
    card.className = 'table-card';
    const chars = (att.char_count || 0).toLocaleString('pt-BR');
    const pages = att.page_count ? ` · ${att.page_count} página(s)` : '';
    const preview = att.preview || '';
    card.innerHTML = `
      <div class="table-card-header">
        <span class="table-card-icon">📄</span>
        <div class="table-card-meta">
          <div class="table-card-title">${escapeHtml(att.filename || 'documento')}</div>
          <div class="table-card-sub">${chars} caracteres${pages}</div>
        </div>
      </div>
      <details class="attachment-summary" style="margin:0">
        <summary>Mostrar conteúdo extraído (markdown)</summary>
        <div class="attachment-summary-body" style="max-height:320px;overflow:auto"></div>
      </details>
    `;
    const body = card.querySelector('.attachment-summary-body');
    renderMarkdownInto(body, preview);
    return card;
  }

  // ── Tool calls (idêntico ao anterior) ──────────────────────
  function renderToolCall(tc) {
    const el = document.createElement('div');
    const hasError = tc.error && tc.error.length > 0;
    el.className = `tool-call ${hasError ? 'has-error' : ''}`;

    const icon = iconForTool(tc.tool);
    const args = tc.args || {};

    // executar_pandas: renderiza o `codigo` como bloco de código completo
    // (sem truncar) e os outros args inline.
    const isPandas = tc.tool === 'executar_pandas';
    const codigoArg = isPandas ? args.codigo || '' : '';
    const otherArgsEntries = Object.entries(args).filter(([k]) => !(isPandas && k === 'codigo'));

    const argsStr = otherArgsEntries
      .map(([k, v]) => {
        const val = typeof v === 'string' ? v : JSON.stringify(v);
        return `<span class="tool-arg-key">${escapeHtml(k)}</span>: <span class="tool-arg-val">${escapeHtml(truncate(String(val), 80))}</span>`;
      })
      .join(' · ');

    const resultStr = hasError ? tc.error : (tc.result || '');
    let prettyResult = resultStr;
    try {
      const parsed = JSON.parse(resultStr);
      prettyResult = JSON.stringify(parsed, null, 2);
    } catch { /* mantém raw */ }

    const durationStr = tc.duration_ms ? ` · ${tc.duration_ms}ms` : '';

    let argsBlock = '';
    if (codigoArg) {
      argsBlock += `
        <div class="tool-call-row">
          <span class="tool-call-label">código</span>
          <pre class="tool-call-code">${escapeHtml(codigoArg)}</pre>
        </div>`;
    }
    if (argsStr) {
      argsBlock += `
        <div class="tool-call-row">
          <span class="tool-call-label">argumentos</span>
          <div class="tool-call-value">${argsStr}</div>
        </div>`;
    }
    if (!codigoArg && !argsStr) {
      argsBlock = `
        <div class="tool-call-row">
          <span class="tool-call-label">argumentos</span>
          <div class="tool-call-value"><em>sem argumentos</em></div>
        </div>`;
    }

    el.innerHTML = `
      <div class="tool-call-header">
        <span class="tool-call-icon">${icon}</span>
        <code class="tool-call-name">${escapeHtml(tc.tool)}()</code>
        <span class="tool-call-status">
          <span class="status-dot ${hasError ? 'error' : 'ok'}"></span>
          ${hasError ? 'erro' : 'executado'}${durationStr}
        </span>
        <svg class="tool-call-chev" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
          <polyline points="6 9 12 15 18 9"/>
        </svg>
      </div>
      <div class="tool-call-body">
        ${argsBlock}
        <div class="tool-call-row">
          <span class="tool-call-label">${hasError ? 'erro' : 'retorno'}</span>
          <pre class="tool-call-value mono">${escapeHtml(truncate(prettyResult, 2000))}</pre>
        </div>
      </div>`;

    // Renderiza tool calls aninhadas (sub-agente do call_agent).
    const nested = tc.nested_tool_calls || [];
    if (nested.length > 0) {
      const body = el.querySelector('.tool-call-body');
      const wrap = document.createElement('div');
      wrap.className = 'tool-call-nested-wrap';
      wrap.innerHTML = `<span class="tool-call-nested-label">🔽 Tools do sub-agente</span>`;
      nested.forEach(ntc => wrap.appendChild(renderToolCall(ntc)));
      body.appendChild(wrap);
    }

    el.querySelector('.tool-call-header').addEventListener('click', (e) => {
      // Não abre/fecha o pai quando clica num tool-call aninhado
      if (e.target.closest('.tool-call-nested-wrap')) return;
      el.classList.toggle('open');
    });

    return el;
  }

  function renderExportCard(payload) {
    const fmt = (payload.formato || '').toLowerCase();
    const filename = payload.filename || 'export';
    const url = payload.download_url;
    const sizeKb = payload.size_kb != null ? payload.size_kb : null;
    const sizeStr = sizeKb == null ? '' : (sizeKb >= 1024 ? `${(sizeKb / 1024).toFixed(1)} MB` : `${sizeKb} KB`);

    // PDF descreve páginas/título; tabelas descrevem linhas × colunas.
    let detailTag;
    let fileIcon;
    let iconClass;
    if (fmt === 'pdf') {
      const pgs = payload.paginas != null ? payload.paginas : null;
      detailTag = pgs != null ? `${pgs} página${pgs === 1 ? '' : 's'}` : 'Documento PDF';
      fileIcon = '📕';
      iconClass = 'pdf';
    } else {
      const linhas = payload.linhas != null ? payload.linhas.toLocaleString('pt-BR') : '?';
      const colunas = payload.colunas != null ? payload.colunas : '?';
      detailTag = `${linhas} linhas · ${colunas} colunas`;
      fileIcon = fmt === 'xlsx' ? '📊' : '📄';
      iconClass = fmt === 'xlsx' ? 'xlsx' : 'csv';
    }

    const downloadIcon = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/></svg>`;

    return `
      <div class="export-card">
        <div class="export-card-icon ${iconClass}">${fileIcon}</div>
        <div class="export-card-info">
          <div class="export-card-filename" title="${escapeHtml(filename)}">${escapeHtml(filename)}</div>
          <div class="export-card-meta">
            <span class="export-card-meta-tag format">${escapeHtml(fmt || '?')}</span>
            <span class="export-card-meta-tag">${escapeHtml(detailTag)}</span>
            ${sizeStr ? `<span class="export-card-meta-tag">${sizeStr}</span>` : ''}
          </div>
        </div>
        <a class="btn-download" href="${escapeHtml(url)}" download="${escapeHtml(filename)}">
          ${downloadIcon} Baixar
        </a>
      </div>`;
  }

  // ── Card-gráfico (imagem matplotlib com expandir + download PNG) ──
  function renderChartCard(att) {
    // Ícone + rótulo amigável por tipo (default = barras p/ retrocompat).
    const CHART_META = {
      barras:     { icon: '📊', label: 'Gráfico de barras' },
      linha:      { icon: '📈', label: 'Gráfico de linhas' },
      area:       { icon: '📈', label: 'Gráfico de área' },
      pizza:      { icon: '🥧', label: 'Gráfico de pizza' },
      dispersao:  { icon: '✳️', label: 'Gráfico de dispersão' },
      histograma: { icon: '📊', label: 'Histograma' },
      boxplot:    { icon: '📦', label: 'Boxplot' },
      heatmap:    { icon: '🌡️', label: 'Mapa de calor' },
    };
    const type = att.chart_type || att.tipo || 'barras';
    const meta = CHART_META[type] || CHART_META.barras;
    const titulo = att.titulo || meta.label;
    const img = att.image || '';
    const bits = [];
    if (att.n_categorias != null) {
      bits.push(`${att.n_categorias} categoria${att.n_categorias === 1 ? '' : 's'}`);
    }
    if (att.n_series && att.n_series > 1) {
      bits.push(`${att.n_series} séries`);
      if (att.empilhado) bits.push('empilhado');
    }
    if (att.orientacao === 'horizontal') bits.push('horizontal');
    const sub = bits.length ? bits.join(' · ') : meta.label;

    const card = document.createElement('div');
    card.className = 'chart-card';
    card.innerHTML = `
      <div class="chart-card-header">
        <span class="chart-card-icon">${meta.icon}</span>
        <div class="chart-card-meta">
          <div class="chart-card-title"></div>
          <div class="chart-card-sub"></div>
        </div>
        <div class="chart-card-actions">
          <button class="chart-card-btn" data-action="png" title="Baixar PNG">PNG</button>
          <button class="chart-card-btn primary" data-action="expand" title="Ver em tela cheia">Expandir</button>
        </div>
      </div>
      <div class="chart-card-viewport">
        <img class="chart-card-img" alt="Gráfico de barras" />
      </div>`;

    card.querySelector('.chart-card-title').textContent = titulo;
    card.querySelector('.chart-card-sub').textContent = sub;
    const imgEl = card.querySelector('.chart-card-img');
    imgEl.src = img;

    function safeStem() {
      return (titulo || 'grafico').toLowerCase()
        .normalize('NFD').replace(/[\u0300-\u036f]/g, '')
        .replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 60) || 'grafico';
    }

    card.querySelector('[data-action="png"]').addEventListener('click', () => {
      if (!img) return;
      const a = document.createElement('a');
      a.href = img;
      a.download = `${safeStem()}.png`;
      document.body.appendChild(a);
      a.click();
      a.remove();
    });

    let backdrop = null;
    let placeholder = null;   // marca a posição original do card no fluxo
    function closeExpand() {
      if (!card.classList.contains('expanded')) return;
      card.classList.remove('expanded');
      // Devolve o card ao seu lugar original na conversa.
      if (placeholder && placeholder.parentNode) {
        placeholder.parentNode.replaceChild(card, placeholder);
      }
      placeholder = null;
      if (backdrop) { backdrop.remove(); backdrop = null; }
      const btn = card.querySelector('[data-action="expand"]');
      if (btn) btn.textContent = 'Expandir';
    }
    card.querySelector('[data-action="expand"]').addEventListener('click', (e) => {
      const btn = e.currentTarget;
      if (card.classList.contains('expanded')) { closeExpand(); return; }
      backdrop = document.createElement('div');
      backdrop.className = 'chart-card-backdrop';
      backdrop.addEventListener('click', closeExpand);
      document.body.appendChild(backdrop);
      // Move o card para o <body> antes de expandir. As mensagens têm
      // `animation: fadeSlideIn ... forwards`, cujo `transform` final cria
      // um containing block que prenderia o `position: fixed` do card —
      // fazendo o card expandido "sumir" (posicionado na linha, não na tela).
      placeholder = document.createComment('chart-card-placeholder');
      card.parentNode.replaceChild(placeholder, card);
      document.body.appendChild(card);
      card.classList.add('expanded');
      btn.textContent = 'Fechar';
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && card.classList.contains('expanded')) closeExpand();
    });

    // Clicar na imagem também expande/recolhe.
    imgEl.addEventListener('click', () => {
      card.querySelector('[data-action="expand"]').click();
    });

    return card;
  }

  // ── Card-fluxograma (diagrama Mermaid com expandir + download) ──
  let _mermaidSeq = 0;
  function renderMermaidCard(att) {
    const code = att.code || '';
    const titulo = att.titulo || 'Fluxograma do processo';
    const linhas = att.linhas != null ? att.linhas : code.split('\n').length;
    const uid = 'mmd-' + (++_mermaidSeq);

    const card = document.createElement('div');
    card.className = 'mermaid-card';
    card.innerHTML = `
      <div class="mermaid-card-header">
        <span class="mermaid-card-icon">🗺️</span>
        <div class="mermaid-card-meta">
          <div class="mermaid-card-title">${escapeHtml(titulo)}</div>
          <div class="mermaid-card-sub">Fluxograma · ${linhas} linha${linhas === 1 ? '' : 's'}</div>
        </div>
        <div class="mermaid-card-actions">
          <button class="mermaid-card-btn" data-action="png" title="Baixar PNG">PNG</button>
          <button class="mermaid-card-btn" data-action="svg" title="Baixar SVG">SVG</button>
          <button class="mermaid-card-btn" data-action="mmd" title="Baixar código Mermaid (.mmd)">.mmd</button>
          <button class="mermaid-card-btn primary" data-action="expand" title="Expandir">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="15 3 21 3 21 9"/><polyline points="9 21 3 21 3 15"/>
              <line x1="21" y1="3" x2="14" y2="10"/><line x1="3" y1="21" x2="10" y2="14"/>
            </svg>
            <span>Expandir</span>
          </button>
        </div>
      </div>
      <div class="mermaid-card-viewport">
        <div class="mermaid-stage"><div class="mermaid-render" id="${uid}"></div></div>
        <div class="mermaid-zoom-controls">
          <button class="mermaid-zoom-btn" data-zoom="out" title="Diminuir (scroll ↓)">−</button>
          <span class="mermaid-zoom-level" title="Zoom atual">100%</span>
          <button class="mermaid-zoom-btn" data-zoom="in" title="Aumentar (scroll ↑)">+</button>
          <button class="mermaid-zoom-btn" data-zoom="fit" title="Ajustar à tela">⤢</button>
        </div>
        <div class="mermaid-zoom-hint">Scroll para zoom · arraste para mover</div>
      </div>
    `;

    const viewport = card.querySelector('.mermaid-card-viewport');
    const stage = card.querySelector('.mermaid-stage');
    const renderTarget = card.querySelector('.mermaid-render');
    const expandBtn = card.querySelector('[data-action="expand"]');
    const zoomLevelEl = card.querySelector('.mermaid-zoom-level');

    const state = { svg: '', expanded: false, origParent: null, origNext: null, backdrop: null };

    // ── Zoom & Pan ──────────────────────────────────────────
    const MIN_SCALE = 0.2, MAX_SCALE = 5;
    const view = { scale: 1, x: 0, y: 0 };

    function applyTransform() {
      stage.style.transform = `translate(${view.x}px, ${view.y}px) scale(${view.scale})`;
      if (zoomLevelEl) zoomLevelEl.textContent = Math.round(view.scale * 100) + '%';
    }

    function clampScale(s) { return Math.min(MAX_SCALE, Math.max(MIN_SCALE, s)); }

    // Zoom mantendo o ponto sob o cursor fixo (cx, cy relativos ao viewport).
    function zoomAt(cx, cy, factor) {
      const next = clampScale(view.scale * factor);
      const ratio = next / view.scale;
      view.x = cx - (cx - view.x) * ratio;
      view.y = cy - (cy - view.y) * ratio;
      view.scale = next;
      applyTransform();
    }

    function zoomCentered(factor) {
      const r = viewport.getBoundingClientRect();
      zoomAt(r.width / 2, r.height / 2, factor);
    }

    // Ajusta o diagrama para caber no viewport e centraliza.
    function fitToView() {
      const svgEl = renderTarget.querySelector('svg');
      if (!svgEl) return;
      const vp = viewport.getBoundingClientRect();
      // Dimensões naturais do SVG (sem o transform atual).
      const natW = svgEl.viewBox && svgEl.viewBox.baseVal && svgEl.viewBox.baseVal.width
        ? svgEl.viewBox.baseVal.width : svgEl.getBBox().width;
      const natH = svgEl.viewBox && svgEl.viewBox.baseVal && svgEl.viewBox.baseVal.height
        ? svgEl.viewBox.baseVal.height : svgEl.getBBox().height;
      if (!natW || !natH) return;
      const pad = 32;
      const s = clampScale(Math.min((vp.width - pad) / natW, (vp.height - pad) / natH, MAX_SCALE));
      view.scale = s;
      view.x = (vp.width - natW * s) / 2;
      view.y = (vp.height - natH * s) / 2;
      applyTransform();
    }

    function onWheel(e) {
      e.preventDefault();
      const r = viewport.getBoundingClientRect();
      const factor = e.deltaY < 0 ? 1.12 : 1 / 1.12;
      zoomAt(e.clientX - r.left, e.clientY - r.top, factor);
    }

    // Arrastar para mover (mouse e toque).
    const drag = { active: false, sx: 0, sy: 0, ox: 0, oy: 0, pointers: new Map(), pinchDist: 0 };

    function onPointerDown(e) {
      drag.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (drag.pointers.size === 1) {
        drag.active = true;
        drag.sx = e.clientX; drag.sy = e.clientY;
        drag.ox = view.x; drag.oy = view.y;
        viewport.classList.add('grabbing');
      } else if (drag.pointers.size === 2) {
        drag.active = false;
        const pts = [...drag.pointers.values()];
        drag.pinchDist = Math.hypot(pts[0].x - pts[1].x, pts[0].y - pts[1].y);
      }
      viewport.setPointerCapture && viewport.setPointerCapture(e.pointerId);
    }

    function onPointerMove(e) {
      if (!drag.pointers.has(e.pointerId)) return;
      drag.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });

      if (drag.pointers.size === 2) {
        // Pinch-zoom (toque).
        const pts = [...drag.pointers.values()];
        const dist = Math.hypot(pts[0].x - pts[1].x, pts[0].y - pts[1].y);
        if (drag.pinchDist > 0) {
          const r = viewport.getBoundingClientRect();
          const midX = (pts[0].x + pts[1].x) / 2 - r.left;
          const midY = (pts[0].y + pts[1].y) / 2 - r.top;
          zoomAt(midX, midY, dist / drag.pinchDist);
        }
        drag.pinchDist = dist;
        return;
      }

      if (!drag.active) return;
      view.x = drag.ox + (e.clientX - drag.sx);
      view.y = drag.oy + (e.clientY - drag.sy);
      applyTransform();
    }

    function onPointerUp(e) {
      drag.pointers.delete(e.pointerId);
      if (drag.pointers.size < 2) drag.pinchDist = 0;
      if (drag.pointers.size === 0) {
        drag.active = false;
        viewport.classList.remove('grabbing');
      }
    }

    viewport.addEventListener('wheel', onWheel, { passive: false });
    viewport.addEventListener('pointerdown', onPointerDown);
    viewport.addEventListener('pointermove', onPointerMove);
    viewport.addEventListener('pointerup', onPointerUp);
    viewport.addEventListener('pointercancel', onPointerUp);
    viewport.addEventListener('dblclick', () => fitToView());

    card.querySelector('[data-zoom="in"]').addEventListener('click', () => zoomCentered(1.25));
    card.querySelector('[data-zoom="out"]').addEventListener('click', () => zoomCentered(1 / 1.25));
    card.querySelector('[data-zoom="fit"]').addEventListener('click', () => fitToView());

    // Render assíncrono do Mermaid → injeta o SVG no alvo.
    (async () => {
      if (!window.mermaid) {
        renderTarget.innerHTML = '<pre class="mermaid-fallback"></pre>';
        renderTarget.querySelector('pre').textContent = code;
        return;
      }
      try {
        const { svg } = await mermaid.render(uid + '-svg', code);
        state.svg = svg;
        renderTarget.innerHTML = svg;
        // Espera o layout assentar antes de ajustar.
        requestAnimationFrame(() => fitToView());
      } catch (e) {
        renderTarget.innerHTML =
          `<div class="mermaid-error">⚠️ Não consegui renderizar o diagrama.</div><pre class="mermaid-fallback"></pre>`;
        renderTarget.querySelector('pre').textContent = code;
        viewport.classList.add('no-pan');
      }
    })();

    function safeStem() {
      return (titulo || 'fluxograma').toLowerCase()
        .normalize('NFD').replace(/[\u0300-\u036f]/g, '')
        .replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 50) || 'fluxograma';
    }

    function triggerDownload(href, filename, revoke) {
      const a = document.createElement('a');
      a.href = href;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      if (revoke) setTimeout(() => URL.revokeObjectURL(href), 1000);
    }

    function downloadBlob(content, mime, ext) {
      const blob = new Blob([content], { type: mime });
      const url = URL.createObjectURL(blob);
      triggerDownload(url, `${safeStem()}.${ext}`, true);
    }

    function downloadSvg() {
      if (!state.svg) return;
      downloadBlob(state.svg, 'image/svg+xml;charset=utf-8', 'svg');
    }

    function downloadMmd() {
      downloadBlob(code, 'text/plain;charset=utf-8', 'mmd');
    }

    // PNG: serializa o SVG numa imagem e desenha num canvas em 2x para nitidez.
    // Usa as dimensões NATURAIS do SVG (viewBox), independentes do zoom atual.
    function downloadPng() {
      if (!state.svg) return;
      const svgEl = renderTarget.querySelector('svg');
      let width = 1200, height = 800;
      if (svgEl && svgEl.viewBox && svgEl.viewBox.baseVal && svgEl.viewBox.baseVal.width) {
        width = svgEl.viewBox.baseVal.width;
        height = svgEl.viewBox.baseVal.height;
      } else if (svgEl) {
        try { const b = svgEl.getBBox(); if (b.width) { width = b.width; height = b.height; } } catch (_) {}
      }
      const scale = 2;
      const img = new Image();
      const svgBlob = new Blob([state.svg], { type: 'image/svg+xml;charset=utf-8' });
      const url = URL.createObjectURL(svgBlob);
      img.onload = () => {
        const canvas = document.createElement('canvas');
        canvas.width = Math.round(width * scale);
        canvas.height = Math.round(height * scale);
        const ctx = canvas.getContext('2d');
        ctx.fillStyle = '#ffffff';
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
        URL.revokeObjectURL(url);
        canvas.toBlob((blob) => {
          if (!blob) return;
          const purl = URL.createObjectURL(blob);
          triggerDownload(purl, `${safeStem()}.png`, true);
        }, 'image/png');
      };
      img.onerror = () => { URL.revokeObjectURL(url); };
      img.src = url;
    }

    function expand() {
      if (state.expanded) return;
      state.expanded = true;
      state.origParent = card.parentNode;
      state.origNext = card.nextSibling;
      const backdrop = document.createElement('div');
      backdrop.className = 'table-card-backdrop';
      backdrop.addEventListener('click', minimize);
      document.body.appendChild(backdrop);
      document.body.appendChild(card);
      state.backdrop = backdrop;
      card.classList.add('expanded');
      document.body.classList.add('modal-open');
      expandBtn.querySelector('span').textContent = 'Minimizar';
      document.addEventListener('keydown', onKey);
      requestAnimationFrame(() => fitToView());
    }

    function minimize() {
      if (!state.expanded) return;
      state.expanded = false;
      card.classList.remove('expanded');
      document.body.classList.remove('modal-open');
      expandBtn.querySelector('span').textContent = 'Expandir';
      document.removeEventListener('keydown', onKey);
      if (state.backdrop) { state.backdrop.remove(); state.backdrop = null; }
      if (state.origParent) state.origParent.insertBefore(card, state.origNext);
      requestAnimationFrame(() => fitToView());
    }

    function onKey(e) { if (e.key === 'Escape') minimize(); }

    expandBtn.addEventListener('click', () => state.expanded ? minimize() : expand());
    card.querySelector('[data-action="png"]').addEventListener('click', downloadPng);
    card.querySelector('[data-action="svg"]').addEventListener('click', downloadSvg);
    card.querySelector('[data-action="mmd"]').addEventListener('click', downloadMmd);

    return card;
  }

  function iconForTool(name) {
    const map = {
      'thinking': '🧠',
      'ask_human': '❓',
      'consulta_aws': '🗄️',
      'descrever_dataset': '🔎',
      'normalizar_coluna': '✨',
      'filtrar_por_termo': '🔬',
      'contar_keywords': '🔠',
      'contem_termo': '✅',
      'agrupar': '📊',
      'regex_extrair': '🧩',
      'call_agent': '🤝',
      'analise_massiva_llm': '🚀',
      'executar_pandas': '🐍',
      'exportar_dataset': '💾',
      'gerar_fluxograma': '🗺️',
      'descrever_documento': '📄',
      'ler_documento': '📖',
      'buscar_no_documento': '🔎',
      'extrair_tabelas_do_documento': '📊',
    };
    return map[name] || '⚡';
  }

  function escapeHtml(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function truncate(s, max) {
    return s.length <= max ? s : s.slice(0, max) + '…';
  }

  function appendTyping() {
    const row = document.createElement('div');
    row.className = 'typing-row';
    row.innerHTML = `
      <div class="msg-avatar assistant">🛡️</div>
      <div class="msg-content-wrap">
        <div class="msg-author">Multi-Agentes Auditoria</div>
        <div class="typing-dots"><span></span><span></span><span></span></div>
        <div class="progress-log"></div>
      </div>`;
    messagesArea.appendChild(row);
    scrollToBottom();
    return row;
  }

  // Atualiza o logzinho de progresso ao vivo dentro do balão "pensando".
  // Eventos de progresso contínuo (ex.: análise massiva "3 de 10") atualizam
  // a MESMA linha; os demais empilham, mantendo só as últimas.
  function updateTypingProgress(typingEl, evt) {
    if (!typingEl) return;
    const log = typingEl.querySelector('.progress-log');
    if (!log) return;

    const icon = evt.icon || (evt.stage === 'thinking' ? '🧠'
      : evt.stage === 'massiva' ? '🚀' : '⚙️');
    const text = evt.text || '';

    if (evt.stage === 'massiva') {
      // Linha única que se atualiza (barra de progresso textual).
      let line = log.querySelector('.progress-line.massiva');
      if (!line) {
        line = document.createElement('div');
        line.className = 'progress-line massiva';
        log.appendChild(line);
      }
      const pct = evt.total ? Math.round((evt.current / evt.total) * 100) : 0;
      line.innerHTML = `<span class="progress-icon">${icon}</span><span>${escapeHtml(text)}</span><span class="progress-pct">${pct}%</span>`;
    } else {
      const line = document.createElement('div');
      line.className = 'progress-line';
      line.innerHTML = `<span class="progress-icon">${icon}</span><span>${escapeHtml(text)}</span>`;
      log.appendChild(line);
      // Mantém só as últimas 5 linhas pra não crescer demais.
      while (log.querySelectorAll('.progress-line').length > 5) {
        log.removeChild(log.firstChild);
      }
    }
    scrollToBottom();
  }

  function scrollToBottom() {
    messagesArea.scrollTop = messagesArea.scrollHeight;
  }

  // ════════════════════════════════════════════════════════════
  // Agente da sessão (criado só para este chat, não democratizado)
  // ════════════════════════════════════════════════════════════
  const saBtn = document.getElementById('session-agent-btn');
  const saBackdrop = document.getElementById('sa-modal-backdrop');
  const saClose = document.getElementById('sa-modal-close');
  const saCancel = document.getElementById('sa-cancel-btn');
  const saSave = document.getElementById('sa-save-btn');
  const saDelete = document.getElementById('sa-delete-btn');
  const saExport = document.getElementById('sa-export-btn');
  const saImport = document.getElementById('sa-import-btn');
  const saImportInput = document.getElementById('sa-import-input');
  const saIcon = document.getElementById('sa-icon');
  const saName = document.getElementById('sa-name');
  const saPrompt = document.getElementById('sa-prompt');
  const saGuardrails = document.getElementById('sa-guardrails');
  const saModel = document.getElementById('sa-model');
  const saTemp = document.getElementById('sa-temp');
  const saTempVal = document.getElementById('sa-temp-val');
  const saToolsGrid = document.getElementById('sa-tools-grid');
  const saToolsCount = document.getElementById('sa-tools-count');
  const saDocsList = document.getElementById('sa-docs-list');
  const saDocsCount = document.getElementById('sa-docs-count');
  const saDocAddBtn = document.getElementById('sa-doc-add-btn');
  const saDocInput = document.getElementById('sa-doc-input');

  let saConfigLoaded = false;
  // Documentos anexados ao agente no formulário corrente.
  // Itens recém-anexados trazem `markdown` (conteúdo extraído); itens já
  // salvos vêm só como resumo (sem markdown) e o backend recupera o conteúdo.
  let saDocs = [];

  function updateSessionAgentBtn() {
    if (!saBtn) return;
    saBtn.classList.toggle('has-agent', hasSessionAgent);
    saBtn.title = hasSessionAgent
      ? 'Editar o agente desta sessão'
      : 'Criar seu agente para essa sessão';
  }

  // Carrega modelos + tools (de /api/config/) para popular o formulário.
  async function ensureSaConfig() {
    if (saConfigLoaded) return;
    try {
      const res = await fetch('/api/config/');
      const data = await res.json();
      // Modelos
      saModel.innerHTML = '';
      (data.models || []).forEach(m => {
        const opt = document.createElement('option');
        opt.value = m; opt.textContent = m;
        saModel.appendChild(opt);
      });
      // Tools (checkboxes)
      saToolsGrid.innerHTML = '';
      (data.tools || []).forEach(t => {
        const chip = document.createElement('label');
        chip.className = 'sa-tool-chip';
        chip.innerHTML = `
          <input type="checkbox" value="${escapeHtml(t.slug)}" />
          <span class="sa-tool-ico">${escapeHtml(t.icon || '⚡')}</span>
          <span class="sa-tool-name" title="${escapeHtml(t.description || t.name)}">${escapeHtml(t.name)}</span>`;
        const cb = chip.querySelector('input');
        cb.addEventListener('change', () => {
          chip.classList.toggle('checked', cb.checked);
          updateSaToolsCount();
        });
        saToolsGrid.appendChild(chip);
      });
      saConfigLoaded = true;
    } catch (e) { /* silencioso */ }
  }

  function updateSaToolsCount() {
    const n = saToolsGrid.querySelectorAll('input:checked').length;
    saToolsCount.textContent = n ? `· ${n} selecionada${n === 1 ? '' : 's'}` : '';
  }

  function getFormData() {
    const tools = Array.from(saToolsGrid.querySelectorAll('input:checked')).map(c => c.value);
    // Docs com markdown (recém-anexados) vão completos; os já salvos vão como
    // referência (só filename) — o backend recupera o conteúdo pelo nome.
    const documents = saDocs.map(d => (
      d.markdown != null
        ? { filename: d.filename, markdown: d.markdown, page_count: d.page_count ?? null }
        : { filename: d.filename }
    ));
    return {
      name: saName.value.trim() || 'Meu agente',
      icon: saIcon.value.trim() || '🤖',
      system_prompt: saPrompt.value,
      guardrails: saGuardrails.value,
      model: saModel.value,
      temperature: parseFloat(saTemp.value),
      tools_enabled: tools,
      documents,
    };
  }

  function fmtCharCount(n) {
    if (n == null) return '';
    if (n >= 1000) return `${(n / 1000).toFixed(1)}k chars`;
    return `${n} chars`;
  }

  function renderDocs() {
    if (!saDocsList) return;
    saDocsList.innerHTML = '';
    saDocs.forEach((d, i) => {
      const pages = d.page_count ? ` · ${d.page_count} pág.` : '';
      const meta = `${fmtCharCount(d.char_count)}${pages}`;
      const row = document.createElement('div');
      row.className = 'sa-doc-chip';
      row.innerHTML = `
        <span class="sa-doc-ico">📄</span>
        <span class="sa-doc-name" title="${escapeHtml(d.filename)}">${escapeHtml(d.filename)}</span>
        <span class="sa-doc-meta">${escapeHtml(meta)}</span>
        <button class="sa-doc-remove" type="button" title="Remover" data-i="${i}">×</button>`;
      row.querySelector('.sa-doc-remove').addEventListener('click', () => {
        saDocs.splice(i, 1);
        renderDocs();
      });
      saDocsList.appendChild(row);
    });
    if (saDocsCount) {
      const n = saDocs.length;
      saDocsCount.textContent = n ? `· ${n} documento${n === 1 ? '' : 's'}` : '';
    }
  }

  async function uploadSaDocument(file) {
    const placeholder = { filename: file.name, char_count: null, _loading: true };
    saDocs.push(placeholder);
    renderDocs();
    try {
      const fd = new FormData();
      fd.append('file', file);
      const res = await fetch('/api/session-agent/extract-document/', {
        method: 'POST',
        headers: { 'X-CSRFToken': getCookie('csrftoken') },
        body: fd,
      });
      const data = await res.json();
      const idx = saDocs.indexOf(placeholder);
      if (data.status === 'success' && data.document) {
        if (idx !== -1) saDocs[idx] = data.document;
      } else {
        if (idx !== -1) saDocs.splice(idx, 1);
        alert('Não foi possível anexar "' + file.name + '": ' + (data.message || 'erro desconhecido'));
      }
    } catch (e) {
      const idx = saDocs.indexOf(placeholder);
      if (idx !== -1) saDocs.splice(idx, 1);
      alert('Falha ao anexar "' + file.name + '": ' + e.message);
    }
    renderDocs();
  }

  function fillForm(a) {
    saName.value = a.name || '';
    saIcon.value = a.icon || '🤖';
    saPrompt.value = a.system_prompt || '';
    saGuardrails.value = a.guardrails || '';
    if (a.model && [...saModel.options].some(o => o.value === a.model)) {
      saModel.value = a.model;
    }
    const t = (a.temperature != null) ? a.temperature : 0.7;
    saTemp.value = t;
    saTempVal.textContent = Number(t).toFixed(2);
    const set = new Set(a.tools_enabled || []);
    saToolsGrid.querySelectorAll('.sa-tool-chip').forEach(chip => {
      const cb = chip.querySelector('input');
      cb.checked = set.has(cb.value);
      chip.classList.toggle('checked', cb.checked);
    });
    updateSaToolsCount();
    saDocs = Array.isArray(a.documents) ? a.documents.map(d => ({ ...d })) : [];
    renderDocs();
  }

  function resetForm() {
    fillForm({ name: '', icon: '🤖', system_prompt: '', guardrails: '', temperature: 0.7, tools_enabled: [], documents: [] });
  }

  async function openSaModal() {
    if (!saBackdrop) return;
    await ensureSaConfig();
    // Se a conversa já tem agente, carrega para edição; senão, formulário limpo.
    if (conversationId && hasSessionAgent) {
      try {
        const res = await fetch(`/api/conversations/${conversationId}/session-agent/`);
        if (res.ok) {
          const data = await res.json();
          if (data.status === 'success') fillForm(data.agent);
        } else {
          resetForm();
        }
      } catch { resetForm(); }
    } else {
      resetForm();
    }
    saDelete.hidden = !(conversationId && hasSessionAgent);
    saBackdrop.hidden = false;
    document.body.classList.add('modal-open');
    saName.focus();
  }

  function closeSaModal() {
    if (!saBackdrop) return;
    saBackdrop.hidden = true;
    document.body.classList.remove('modal-open');
  }

  async function saveSessionAgent() {
    const payload = getFormData();
    saSave.disabled = true;
    saSave.textContent = 'Salvando…';
    try {
      let url, isNew = false;
      if (conversationId) {
        url = `/api/conversations/${conversationId}/session-agent/save/`;
      } else {
        // Chat novo: cria a conversa já com o agente da sessão.
        url = '/api/session-agent/create-conversation/';
        isNew = true;
      }
      const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (data.status === 'success') {
        if (isNew && data.conversation_id) {
          conversationId = data.conversation_id;
          history.replaceState(null, '', '/?c=' + conversationId);
          if (welcomeState && welcomeState.parentNode) welcomeState.remove();
        }
        hasSessionAgent = true;
        updateSessionAgentBtn();
        if (window.refreshHistory) window.refreshHistory();
        closeSaModal();
      } else {
        alert('Erro ao salvar: ' + (data.message || 'desconhecido'));
      }
    } catch (e) {
      alert('Falha ao salvar o agente: ' + e.message);
    }
    saSave.disabled = false;
    saSave.textContent = 'Salvar agente';
  }

  async function deleteSessionAgent() {
    if (!conversationId || !hasSessionAgent) { closeSaModal(); return; }
    if (!confirm('Excluir o agente desta sessão? Isso não afeta os agentes do sistema.')) return;
    try {
      await fetch(`/api/conversations/${conversationId}/session-agent/delete/`, {
        method: 'DELETE',
        headers: { 'X-CSRFToken': getCookie('csrftoken') },
      });
      hasSessionAgent = false;
      updateSessionAgentBtn();
      closeSaModal();
    } catch (e) { alert('Falha ao excluir: ' + e.message); }
  }

  // Exporta a config atual do formulário como arquivo .json reusável.
  function exportSessionAgent() {
    const data = getFormData();
    const bundle = {
      _kind: 'tech-auditor.session-agent',
      _version: 1,
      ...data,
    };
    const blob = new Blob([JSON.stringify(bundle, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    const stem = (data.name || 'agente').toLowerCase()
      .normalize('NFD').replace(/[\u0300-\u036f]/g, '')
      .replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 40) || 'agente';
    a.href = url;
    a.download = `agente_${stem}.json`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  // Importa um .json exportado e preenche o formulário (não salva sozinho).
  function importSessionAgent(file) {
    const reader = new FileReader();
    reader.onload = (e) => {
      try {
        const data = JSON.parse(e.target.result);
        if (data._kind && data._kind !== 'tech-auditor.session-agent') {
          if (!confirm('Este arquivo não parece ser um agente do Multi-Agentes Auditoria. Importar mesmo assim?')) return;
        }
        fillForm(data);
      } catch (err) {
        alert('Arquivo inválido: ' + err.message);
      }
    };
    reader.readAsText(file);
  }

  if (saBtn) saBtn.addEventListener('click', openSaModal);
  if (saClose) saClose.addEventListener('click', closeSaModal);
  if (saCancel) saCancel.addEventListener('click', closeSaModal);
  if (saSave) saSave.addEventListener('click', saveSessionAgent);
  if (saDelete) saDelete.addEventListener('click', deleteSessionAgent);
  if (saExport) saExport.addEventListener('click', exportSessionAgent);
  if (saDocAddBtn) saDocAddBtn.addEventListener('click', () => saDocInput.click());
  if (saDocInput) {
    saDocInput.addEventListener('change', () => {
      const files = Array.from(saDocInput.files || []);
      files.forEach(f => uploadSaDocument(f));
      saDocInput.value = '';
    });
  }
  if (saImport) saImport.addEventListener('click', () => saImportInput.click());
  if (saImportInput) {
    saImportInput.addEventListener('change', () => {
      const f = saImportInput.files && saImportInput.files[0];
      if (f) importSessionAgent(f);
      saImportInput.value = '';
    });
  }
  if (saTemp) {
    saTemp.addEventListener('input', () => {
      saTempVal.textContent = Number(saTemp.value).toFixed(2);
    });
  }
  if (saBackdrop) {
    saBackdrop.addEventListener('click', (e) => {
      if (e.target === saBackdrop) closeSaModal();
    });
  }
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && saBackdrop && !saBackdrop.hidden) closeSaModal();
  });

  updateSessionAgentBtn();

  // ── Bases de conhecimento (RAG) ──────────────────────────
  const KB_MAX = 10;

  function kbEscape(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[c]));
  }

  function renderKbBadge() {
    if (!kbBadge) return;
    const n = activeKbs.length;
    if (n > 0) {
      kbBadge.textContent = String(n);
      kbBadge.hidden = false;
      kbBtn.classList.add('has-active');
    } else {
      kbBadge.hidden = true;
      kbBtn.classList.remove('has-active');
    }
  }

  async function loadConversationKbs() {
    if (!conversationId) return;
    try {
      const res = await fetch(`/api/conversations/${conversationId}/kbs/`);
      if (!res.ok) return;
      const data = await res.json();
      activeKbs = data.active_kbs || [];
      renderKbBadge();
    } catch (e) { /* silencioso */ }
  }

  async function fetchKbCatalog() {
    if (kbCatalog) return kbCatalog;
    try {
      const res = await fetch('/api/kbs/');
      const data = await res.json();
      kbCatalog = (data && data.kbs) || [];
    } catch (e) {
      kbCatalog = [];
    }
    return kbCatalog;
  }

  function renderKbList(filter) {
    const term = (filter || '').trim().toLowerCase();
    const matches = (kbCatalog || []).filter(kb =>
      !term ||
      (kb.name || '').toLowerCase().includes(term) ||
      (kb.description || '').toLowerCase().includes(term)
    );
    if (!matches.length) {
      kbListEl.innerHTML = '<div class="kb-empty">Nenhuma base encontrada.</div>';
      return;
    }
    const atLimit = kbDraft.size >= KB_MAX;
    const rows = matches.map(kb => {
      const checked = kbDraft.has(kb.id);
      const disabled = !checked && atLimit;
      return `
        <tr class="kb-row ${checked ? 'selected' : ''} ${disabled ? 'disabled' : ''}">
          <td class="kb-cell-check">
            <input type="checkbox" data-kb-id="${kbEscape(kb.id)}" ${checked ? 'checked' : ''} ${disabled ? 'disabled' : ''}>
          </td>
          <td class="kb-cell-name">${kbEscape(kb.name || '(sem nome)')}</td>
          <td class="kb-cell-desc" title="${kbEscape(kb.description || '')}">${kbEscape(kb.description || '')}</td>
        </tr>`;
    }).join('');
    kbListEl.innerHTML = `
      <table class="kb-table">
        <thead>
          <tr>
            <th class="kb-cell-check"><span class="sr-only">Selecionar</span></th>
            <th class="kb-cell-name">Nome</th>
            <th class="kb-cell-desc">Descrição</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>`;
    const toggle = (id, on) => {
      if (on) {
        if (kbDraft.size >= KB_MAX) return false;
        kbDraft.add(id);
      } else {
        kbDraft.delete(id);
      }
      updateKbCount();
      renderKbList(kbSearchEl.value);
      return true;
    };
    kbListEl.querySelectorAll('input[type="checkbox"]').forEach(cb => {
      cb.addEventListener('change', () => {
        const id = cb.getAttribute('data-kb-id');
        if (!toggle(id, cb.checked) && cb.checked) cb.checked = false;
      });
    });
    kbListEl.querySelectorAll('tr.kb-row').forEach(row => {
      row.addEventListener('click', (e) => {
        if (e.target.tagName === 'INPUT') return; // checkbox trata o próprio clique
        const cb = row.querySelector('input[type="checkbox"]');
        if (!cb || cb.disabled) return;
        toggle(cb.getAttribute('data-kb-id'), !cb.checked);
      });
    });
  }

  function updateKbCount() {
    kbCountEl.textContent = `${kbDraft.size} selecionada(s)`;
  }

  async function openKbModal() {
    kbDraft = new Set(activeKbs.map(kb => kb.id));
    updateKbCount();
    kbSearchEl.value = '';
    kbBackdrop.hidden = false;
    kbListEl.innerHTML = '<div class="kb-empty">Carregando…</div>';
    await fetchKbCatalog();
    renderKbList('');
  }

  function closeKbModal() { kbBackdrop.hidden = true; }

  async function saveKbSelection() {
    const selected = (kbCatalog || []).filter(kb => kbDraft.has(kb.id));
    activeKbs = selected;
    renderKbBadge();
    closeKbModal();
    // Se a conversa já existe, persiste no servidor. Senão, vai no payload
    // do próximo envio (e o backend grava em conv.state ao criar a conversa).
    if (conversationId) {
      try {
        await fetch(`/api/conversations/${conversationId}/kbs/save/`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
          body: JSON.stringify({ active_kbs: selected }),
        });
      } catch (e) { /* a seleção segue no payload do chat de qualquer forma */ }
    }
  }

  if (kbBtn) {
    kbBtn.addEventListener('click', openKbModal);
    kbCloseBtn.addEventListener('click', closeKbModal);
    kbCancelBtn.addEventListener('click', closeKbModal);
    kbSaveBtn.addEventListener('click', saveKbSelection);
    kbClearBtn.addEventListener('click', () => {
      kbDraft.clear();
      updateKbCount();
      renderKbList(kbSearchEl.value);
    });
    kbSearchEl.addEventListener('input', (e) => renderKbList(e.target.value));
    kbBackdrop.addEventListener('click', (e) => {
      if (e.target === kbBackdrop) closeKbModal();
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && !kbBackdrop.hidden) closeKbModal();
    });
    renderKbBadge();
  }

  // ── Conhecimentos (prompts de especialista) ──────────────
  const KNOWLEDGE_MAX = 10;

  function renderKnowBadge() {
    if (!knowBadge) return;
    const n = activeKnowledge.length;
    if (n > 0) {
      knowBadge.textContent = String(n);
      knowBadge.hidden = false;
      knowBtn.classList.add('has-active');
    } else {
      knowBadge.hidden = true;
      knowBtn.classList.remove('has-active');
    }
  }

  async function loadConversationKnowledge() {
    if (!conversationId) return;
    try {
      const res = await fetch(`/api/conversations/${conversationId}/knowledge/`);
      if (!res.ok) return;
      const data = await res.json();
      activeKnowledge = data.active_knowledge || [];
      renderKnowBadge();
    } catch (e) { /* silencioso */ }
  }

  async function fetchKnowCatalog() {
    // Sempre recarrega: conhecimentos são editados na tela de Configurações.
    try {
      const res = await fetch('/api/knowledge/');
      const data = await res.json();
      knowCatalog = (data && data.knowledge) || [];
    } catch (e) {
      knowCatalog = [];
    }
    return knowCatalog;
  }

  function renderKnowList(filter) {
    const term = (filter || '').trim().toLowerCase();
    const matches = (knowCatalog || []).filter(k =>
      !term ||
      (k.name || '').toLowerCase().includes(term) ||
      (k.description || '').toLowerCase().includes(term)
    );
    if (!matches.length) {
      knowListEl.innerHTML = '<div class="kb-empty">Nenhum conhecimento cadastrado. Crie em Configurações → Conhecimentos.</div>';
      return;
    }
    const atLimit = knowDraft.size >= KNOWLEDGE_MAX;
    const rows = matches.map(k => {
      const checked = knowDraft.has(k.id);
      const disabled = !checked && atLimit;
      const icon = kbEscape(k.icon || '📚');
      return `
        <tr class="kb-row ${checked ? 'selected' : ''} ${disabled ? 'disabled' : ''}">
          <td class="kb-cell-check">
            <input type="checkbox" data-know-id="${kbEscape(k.id)}" ${checked ? 'checked' : ''} ${disabled ? 'disabled' : ''}>
          </td>
          <td class="kb-cell-name">${icon} ${kbEscape(k.name || '(sem nome)')}</td>
          <td class="kb-cell-desc" title="${kbEscape(k.description || '')}">${kbEscape(k.description || '')}</td>
        </tr>`;
    }).join('');
    knowListEl.innerHTML = `
      <table class="kb-table">
        <thead>
          <tr>
            <th class="kb-cell-check"><span class="sr-only">Selecionar</span></th>
            <th class="kb-cell-name">Nome</th>
            <th class="kb-cell-desc">Descrição</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>`;
    const toggle = (id, on) => {
      if (on) {
        if (knowDraft.size >= KNOWLEDGE_MAX) return false;
        knowDraft.add(id);
      } else {
        knowDraft.delete(id);
      }
      updateKnowCount();
      renderKnowList(knowSearchEl.value);
      return true;
    };
    knowListEl.querySelectorAll('input[type="checkbox"]').forEach(cb => {
      cb.addEventListener('change', () => {
        const id = parseInt(cb.getAttribute('data-know-id'), 10);
        if (!toggle(id, cb.checked) && cb.checked) cb.checked = false;
      });
    });
    knowListEl.querySelectorAll('tr.kb-row').forEach(row => {
      row.addEventListener('click', (e) => {
        if (e.target.tagName === 'INPUT') return;
        const cb = row.querySelector('input[type="checkbox"]');
        if (!cb || cb.disabled) return;
        toggle(parseInt(cb.getAttribute('data-know-id'), 10), !cb.checked);
      });
    });
  }

  function updateKnowCount() {
    knowCountEl.textContent = `${knowDraft.size} selecionado(s)`;
    if (knowWarnEl) knowWarnEl.hidden = knowDraft.size <= 1;
  }

  async function openKnowModal() {
    knowDraft = new Set(activeKnowledge.map(k => k.id));
    updateKnowCount();
    knowSearchEl.value = '';
    knowBackdrop.hidden = false;
    knowListEl.innerHTML = '<div class="kb-empty">Carregando…</div>';
    await fetchKnowCatalog();
    renderKnowList('');
  }

  function closeKnowModal() { knowBackdrop.hidden = true; }

  async function saveKnowSelection() {
    // Guarda só os ids ativos; o backend resolve o conteúdo do banco.
    activeKnowledge = Array.from(knowDraft).map(id => ({ id }));
    renderKnowBadge();
    closeKnowModal();
    if (conversationId) {
      try {
        await fetch(`/api/conversations/${conversationId}/knowledge/save/`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
          body: JSON.stringify({ active_knowledge: activeKnowledge }),
        });
      } catch (e) { /* a seleção segue no payload do chat de qualquer forma */ }
    }
  }

  if (knowBtn) {
    knowBtn.addEventListener('click', openKnowModal);
    knowCloseBtn.addEventListener('click', closeKnowModal);
    knowCancelBtn.addEventListener('click', closeKnowModal);
    knowSaveBtn.addEventListener('click', saveKnowSelection);
    knowClearBtn.addEventListener('click', () => {
      knowDraft.clear();
      updateKnowCount();
      renderKnowList(knowSearchEl.value);
    });
    knowSearchEl.addEventListener('input', (e) => renderKnowList(e.target.value));
    knowBackdrop.addEventListener('click', (e) => {
      if (e.target === knowBackdrop) closeKnowModal();
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && !knowBackdrop.hidden) closeKnowModal();
    });
    renderKnowBadge();
  }

  loadAgents();
  loadExistingConversation();
});
