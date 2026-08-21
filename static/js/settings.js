document.addEventListener('DOMContentLoaded', function () {
  let configData = { agents: [], models: [], tools: [] };

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

  // ── Tabs ─────────────────────────────────────────────────────────
  document.querySelectorAll('.config-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      document.querySelectorAll('.config-tab').forEach(t => t.classList.remove('active'));
      document.querySelectorAll('.config-panel').forEach(p => p.classList.remove('active'));
      tab.classList.add('active');
      document.getElementById('panel-' + tab.dataset.tab).classList.add('active');
    });
  });

  // ── Carrega config ───────────────────────────────────────────────
  async function loadConfig() {
    try {
      const res = await fetch('/api/config/');
      configData = await res.json();
      renderGeneral();
      renderMassiva();
      renderAgents();
      renderTools();
    } catch (e) {
      console.error('Erro ao carregar config:', e);
    }
  }

  // ── Configurações gerais ─────────────────────────────────────────
  function renderGeneral() {
    const card = document.getElementById('general-card');
    document.getElementById('general-loading').style.display = 'none';
    card.style.display = 'block';

    const input = document.getElementById('max-iterations');
    const settings = configData.settings || {};
    input.value = settings.max_iterations != null ? settings.max_iterations : 18;

    document.getElementById('general-save-btn').addEventListener('click', saveGeneral);
  }

  async function saveGeneral() {
    const btn = document.getElementById('general-save-btn');
    const status = document.getElementById('general-save-status');
    const input = document.getElementById('max-iterations');

    let n = parseInt(input.value, 10);
    if (!Number.isFinite(n)) n = 18;
    n = Math.max(1, Math.min(100, n));
    input.value = n;

    btn.disabled = true;
    status.textContent = '⏳ Salvando…';
    status.className = 'save-status';

    try {
      const res = await fetch('/api/config/settings/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
        body: JSON.stringify({ max_iterations: n }),
      });
      const data = await res.json();
      if (data.status === 'success') {
        if (data.settings) {
          configData.settings = data.settings;
          input.value = data.settings.max_iterations;
        }
        status.textContent = '✅ Salvo!';
        status.className = 'save-status success';
      } else {
        status.textContent = '❌ ' + (data.message || 'Erro ao salvar');
        status.className = 'save-status error';
      }
    } catch (e) {
      status.textContent = '❌ ' + e.message;
      status.className = 'save-status error';
    }
    btn.disabled = false;
    setTimeout(() => { status.textContent = ''; }, 3000);
  }

  // ── Paralelismo da análise massiva ───────────────────────────────
  // Regras de UX pedidas: teto 10; em 10 fica vermelho e "perigoso".
  function massivaWarnFor(n) {
    if (n >= 10) {
      return {
        cls: 'is-danger',
        ico: '🛑',
        msg: 'Máximo (10). Alto risco de estourar o limite de requisições do provedor (rate limit) e concentrar custo. Use apenas em cargas controladas e por curtos períodos.',
      };
    }
    if (n >= 6) {
      return {
        cls: 'is-warn',
        ico: '⚠️',
        msg: 'Acima do padrão. Acelera, mas aumenta a chance de rate limit conforme o provedor/modelo. Monitore os erros no resultado da análise.',
      };
    }
    return null; // 1–5: seguro, sem aviso
  }

  function updateMassivaWarn(n) {
    const warn = document.getElementById('massiva-warn');
    const input = document.getElementById('massiva-workers');
    const info = massivaWarnFor(n);
    input.classList.toggle('is-danger', n >= 10);
    if (!info) {
      warn.style.display = 'none';
      warn.className = 'massiva-warn';
      warn.innerHTML = '';
      return;
    }
    warn.style.display = 'flex';
    warn.className = 'massiva-warn ' + info.cls;
    warn.innerHTML = `<span class="mw-ico">${info.ico}</span><span>${info.msg}</span>`;
  }

  function renderMassiva() {
    const card = document.getElementById('massiva-card');
    if (!card) return;
    card.style.display = 'block';

    const input = document.getElementById('massiva-workers');
    const settings = configData.settings || {};
    const n = settings.massiva_workers != null ? settings.massiva_workers : 5;
    input.value = n;
    updateMassivaWarn(n);

    input.addEventListener('input', () => {
      let v = parseInt(input.value, 10);
      if (!Number.isFinite(v)) v = 5;
      v = Math.max(1, Math.min(10, v));
      updateMassivaWarn(v);
    });

    document.getElementById('massiva-save-btn').addEventListener('click', saveMassiva);
  }

  async function saveMassiva() {
    const btn = document.getElementById('massiva-save-btn');
    const status = document.getElementById('massiva-save-status');
    const input = document.getElementById('massiva-workers');

    let n = parseInt(input.value, 10);
    if (!Number.isFinite(n)) n = 5;
    n = Math.max(1, Math.min(10, n));
    input.value = n;
    updateMassivaWarn(n);

    // Confirmação extra no valor perigoso (10).
    if (n >= 10 && !confirm('Definir 10 workers é o máximo e pode causar rate limit no provedor. Confirmar?')) {
      return;
    }

    btn.disabled = true;
    status.textContent = '⏳ Salvando…';
    status.className = 'save-status';

    try {
      const res = await fetch('/api/config/settings/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
        body: JSON.stringify({ massiva_workers: n }),
      });
      const data = await res.json();
      if (data.status === 'success') {
        if (data.settings) {
          configData.settings = data.settings;
          input.value = data.settings.massiva_workers;
          updateMassivaWarn(data.settings.massiva_workers);
        }
        status.textContent = '✅ Salvo!';
        status.className = 'save-status success';
      } else {
        status.textContent = '❌ ' + (data.message || 'Erro ao salvar');
        status.className = 'save-status error';
      }
    } catch (e) {
      status.textContent = '❌ ' + e.message;
      status.className = 'save-status error';
    }
    btn.disabled = false;
    setTimeout(() => { status.textContent = ''; }, 3000);
  }

  // ── Renderização Agentes ─────────────────────────────────────────
  function renderAgents() {
    const container = document.getElementById('agents-list');
    document.getElementById('agents-loading').style.display = 'none';
    container.style.display = 'block';
    container.innerHTML = '';

    configData.agents.forEach(agent => container.appendChild(renderAgentCard(agent)));
  }

  function renderAgentCard(agent) {
    const card = document.createElement('div');
    card.className = 'agent-card';
    card.dataset.slug = agent.slug;

    const enabledTools = agent.tools_enabled || [];
    const toolsCheckboxes = configData.tools.map(t => {
      const isOn = enabledTools.includes(t.slug);
      return `
        <label class="tool-check${isOn ? ' is-on' : ''}">
          <input type="checkbox" data-tool-slug="${t.slug}" ${isOn ? 'checked' : ''}>
          <span class="tool-check-icon">${t.icon}</span>
          <span class="tool-check-name">${escapeHtml(t.name)}</span>
          <span class="tool-check-mark" aria-hidden="true"></span>
        </label>`;
    }).join('');
    const toolsOnCount = configData.tools.filter(t => enabledTools.includes(t.slug)).length;
    const toolsTotal = configData.tools.length;

    const modelOptions = configData.models.map(m =>
      `<option value="${m}" ${m === agent.model ? 'selected' : ''}>${m}</option>`
    ).join('');

    card.innerHTML = `
      <div class="agent-card-header">
        <div class="agent-card-icon">${agent.icon}</div>
        <div class="agent-card-title">
          <h3>${escapeHtml(agent.name)} ${agent.is_default ? '<span class="badge-default">padrão</span>' : ''}</h3>
          <p>${escapeHtml(agent.description || '')}</p>
        </div>
      </div>

      <div class="agent-card-body">
        <div class="form-grid">
          <div class="form-group">
            <label>Modelo</label>
            <select class="form-select" data-field="model">${modelOptions}</select>
          </div>
          <div class="form-group">
            <label>Temperatura: <span class="temp-display">${agent.temperature}</span></label>
            <input type="range" class="form-range" data-field="temperature" min="0" max="2" step="0.1" value="${agent.temperature}">
            <div class="range-hint"><span>preciso</span><span>balanceado</span><span>criativo</span></div>
          </div>
        </div>

        <div class="form-group">
          <label>System Prompt</label>
          <textarea class="form-textarea" data-field="system_prompt" rows="10">${escapeHtml(agent.system_prompt)}</textarea>
        </div>

        <div class="form-group tools-group">
          <div class="tools-head">
            <label>Tools habilitadas</label>
            <div class="tools-head-actions">
              <span class="tools-counter"><b>${toolsOnCount}</b> de ${toolsTotal} ativas</span>
              <button type="button" class="tools-bulk" data-bulk="all">marcar todas</button>
              <button type="button" class="tools-bulk" data-bulk="none">limpar</button>
            </div>
          </div>
          <div class="tools-grid">${toolsCheckboxes}</div>
        </div>

        <div class="agent-actions">
          <button class="btn btn-primary save-btn">💾 Salvar</button>
          <span class="save-status"></span>
        </div>
      </div>`;

    // Range live update
    const range = card.querySelector('input[data-field="temperature"]');
    const display = card.querySelector('.temp-display');
    range.addEventListener('input', () => { display.textContent = range.value; });

    // Tools: estado visual + contador ao vivo
    const counter = card.querySelector('.tools-counter b');
    const toolChecks = Array.from(card.querySelectorAll('.tool-check'));
    function refreshToolsCount() {
      const on = card.querySelectorAll('input[data-tool-slug]:checked').length;
      if (counter) counter.textContent = on;
    }
    toolChecks.forEach(lbl => {
      const input = lbl.querySelector('input[data-tool-slug]');
      input.addEventListener('change', () => {
        lbl.classList.toggle('is-on', input.checked);
        refreshToolsCount();
      });
    });
    card.querySelectorAll('.tools-bulk').forEach(btn => {
      btn.addEventListener('click', () => {
        const on = btn.dataset.bulk === 'all';
        toolChecks.forEach(lbl => {
          const input = lbl.querySelector('input[data-tool-slug]');
          input.checked = on;
          lbl.classList.toggle('is-on', on);
        });
        refreshToolsCount();
      });
    });

    // Save
    card.querySelector('.save-btn').addEventListener('click', () => saveAgent(card, agent.slug));

    return card;
  }

  async function saveAgent(card, slug) {
    const status = card.querySelector('.save-status');
    const btn = card.querySelector('.save-btn');
    btn.disabled = true;
    status.textContent = '⏳ Salvando…';
    status.className = 'save-status';

    const payload = {
      model: card.querySelector('select[data-field="model"]').value,
      temperature: parseFloat(card.querySelector('input[data-field="temperature"]').value),
      system_prompt: card.querySelector('textarea[data-field="system_prompt"]').value,
      tools_enabled: Array.from(card.querySelectorAll('input[data-tool-slug]:checked')).map(i => i.dataset.toolSlug),
    };

    try {
      const res = await fetch(`/api/config/agents/${slug}/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (data.status === 'success') {
        status.textContent = '✅ Salvo!';
        status.className = 'save-status success';
      } else {
        status.textContent = '❌ ' + (data.message || 'Erro ao salvar');
        status.className = 'save-status error';
      }
    } catch (e) {
      status.textContent = '❌ ' + e.message;
      status.className = 'save-status error';
    }
    btn.disabled = false;
    setTimeout(() => { status.textContent = ''; }, 3000);
  }

  // ── Renderização Tools ───────────────────────────────────────────
  function renderTools() {
    const container = document.getElementById('tools-list');
    document.getElementById('tools-loading').style.display = 'none';
    container.style.display = 'block';
    container.innerHTML = `<p class="config-info">${configData.tools.length} tools registradas via decorator <code>@tool</code> em <code>tools/</code>.</p>`;

    configData.tools.forEach(t => {
      const params = Object.entries(t.parameters || {})
        .map(([key, meta]) => {
          const isRequired = (t.required || []).includes(key);
          return `
            <li>
              <code>${escapeHtml(key)}</code>
              <span class="param-type">${escapeHtml(meta.type || 'any')}</span>
              ${isRequired ? '<span class="param-req">obrigatório</span>' : '<span class="param-opt">opcional</span>'}
            </li>`;
        }).join('') || '<li><em>sem parâmetros</em></li>';

      const flags = [];
      if (t.is_human_in_loop) flags.push('<span class="tool-flag-tag hil">human-in-loop</span>');
      if (t.uses_session) flags.push('<span class="tool-flag-tag session">usa sessão</span>');

      const card = document.createElement('div');
      card.className = 'tool-info-card';
      card.innerHTML = `
        <div class="tool-info-header">
          <span class="tool-info-icon">${t.icon}</span>
          <code class="tool-info-slug">${escapeHtml(t.slug)}</code>
          <div class="tool-info-flags">${flags.join('')}</div>
        </div>
        <p class="tool-info-desc">${escapeHtml(t.description)}</p>
        <div class="tool-info-params">
          <span class="tool-info-label">Parâmetros</span>
          <ul>${params}</ul>
        </div>`;
      container.appendChild(card);
    });
  }

  function escapeHtml(s) {
    return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  // ── Conhecimentos ────────────────────────────────────────────────
  let knowledgeCache = [];

  async function loadKnowledge() {
    try {
      const res = await fetch('/api/knowledge/');
      const data = await res.json();
      knowledgeCache = (data && data.knowledge) || [];
    } catch (e) {
      knowledgeCache = [];
    }
    renderKnowledge();
  }

  function renderKnowledge() {
    const container = document.getElementById('knowledge-list');
    document.getElementById('knowledge-loading').style.display = 'none';
    container.style.display = 'block';
    container.innerHTML = '';

    if (!knowledgeCache.length) {
      container.innerHTML = '<p class="config-info">Nenhum conhecimento cadastrado ainda. Clique em <b>＋ Novo conhecimento</b> para criar o primeiro.</p>';
      return;
    }
    knowledgeCache.forEach(k => container.appendChild(renderKnowledgeCard(k)));
  }

  function renderKnowledgeCard(k) {
    // k === null => card de criação (ainda sem id)
    const isNew = !k;
    const data = k || { icon: '📚', name: '', description: '', prompt: '' };
    const card = document.createElement('div');
    card.className = 'agent-card knowledge-card';
    if (!isNew) card.dataset.id = data.id;

    card.innerHTML = `
      <div class="agent-card-header">
        <div class="agent-card-icon">${escapeHtml(data.icon || '📚')}</div>
        <div class="agent-card-title">
          <h3>${isNew ? 'Novo conhecimento' : escapeHtml(data.name)}</h3>
          <p>${isNew ? 'Preencha os campos e salve.' : escapeHtml(data.description || '')}</p>
        </div>
      </div>
      <div class="agent-card-body">
        <div class="form-grid">
          <div class="form-group form-group-icon">
            <label>Ícone</label>
            <input type="text" class="form-select" data-field="icon" maxlength="4" value="${escapeHtml(data.icon || '📚')}">
          </div>
          <div class="form-group">
            <label>Nome</label>
            <input type="text" class="form-select" data-field="name" maxlength="120" placeholder="Ex.: Especialista em Fraude de Cartão" value="${escapeHtml(data.name)}">
          </div>
        </div>
        <div class="form-group">
          <label>Descrição curta</label>
          <input type="text" class="form-select" data-field="description" maxlength="240" placeholder="Resumo de 1 linha (aparece no chat)" value="${escapeHtml(data.description || '')}">
        </div>
        <div class="form-group">
          <label>Prompt do conhecimento</label>
          <textarea class="form-textarea" data-field="prompt" rows="12" placeholder="Escreva aqui o prompt completo: contexto de especialista, processo, bases de análise, instruções…">${escapeHtml(data.prompt)}</textarea>
        </div>
        <div class="agent-actions">
          <button class="btn btn-primary know-save-btn">💾 Salvar</button>
          ${isNew ? '' : '<button class="btn btn-danger know-delete-btn">🗑️ Excluir</button>'}
          <span class="save-status"></span>
        </div>
      </div>`;

    card.querySelector('.know-save-btn').addEventListener('click', () => saveKnowledge(card, isNew ? null : data.id));
    const delBtn = card.querySelector('.know-delete-btn');
    if (delBtn) delBtn.addEventListener('click', () => deleteKnowledge(card, data.id, data.name));

    return card;
  }

  function readKnowledgeCard(card) {
    return {
      icon: (card.querySelector('input[data-field="icon"]').value || '📚').trim() || '📚',
      name: card.querySelector('input[data-field="name"]').value.trim(),
      description: card.querySelector('input[data-field="description"]').value.trim(),
      prompt: card.querySelector('textarea[data-field="prompt"]').value.trim(),
    };
  }

  async function saveKnowledge(card, id) {
    const status = card.querySelector('.save-status');
    const btn = card.querySelector('.know-save-btn');
    const payload = readKnowledgeCard(card);

    if (!payload.name) { status.textContent = '❌ Nome é obrigatório'; status.className = 'save-status error'; return; }
    if (!payload.prompt) { status.textContent = '❌ Prompt é obrigatório'; status.className = 'save-status error'; return; }

    btn.disabled = true;
    status.textContent = '⏳ Salvando…';
    status.className = 'save-status';

    const url = id ? `/api/knowledge/${id}/update/` : '/api/knowledge/create/';
    try {
      const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCookie('csrftoken') },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (data.status === 'success') {
        status.textContent = '✅ Salvo!';
        status.className = 'save-status success';
        await loadKnowledge();
      } else {
        status.textContent = '❌ ' + (data.message || 'Erro ao salvar');
        status.className = 'save-status error';
        btn.disabled = false;
      }
    } catch (e) {
      status.textContent = '❌ ' + e.message;
      status.className = 'save-status error';
      btn.disabled = false;
    }
  }

  async function deleteKnowledge(card, id, name) {
    if (!confirm(`Excluir o conhecimento "${name}"? Esta ação não pode ser desfeita.`)) return;
    const status = card.querySelector('.save-status');
    status.textContent = '⏳ Excluindo…';
    status.className = 'save-status';
    try {
      const res = await fetch(`/api/knowledge/${id}/delete/`, {
        method: 'POST',
        headers: { 'X-CSRFToken': getCookie('csrftoken') },
      });
      const data = await res.json();
      if (data.status === 'success') {
        await loadKnowledge();
      } else {
        status.textContent = '❌ ' + (data.message || 'Erro ao excluir');
        status.className = 'save-status error';
      }
    } catch (e) {
      status.textContent = '❌ ' + e.message;
      status.className = 'save-status error';
    }
  }

  const knowledgeNewBtn = document.getElementById('knowledge-new-btn');
  if (knowledgeNewBtn) {
    knowledgeNewBtn.addEventListener('click', () => {
      const container = document.getElementById('knowledge-list');
      container.style.display = 'block';
      // Evita abrir dois formulários de criação ao mesmo tempo.
      if (container.querySelector('.knowledge-card:not([data-id])')) return;
      const emptyMsg = container.querySelector('.config-info');
      if (emptyMsg) emptyMsg.remove();
      const newCard = renderKnowledgeCard(null);
      container.prepend(newCard);
      newCard.querySelector('input[data-field="name"]').focus();
    });
  }

  loadConfig();
  loadKnowledge();
});
