// Gerencia o histórico de conversas na sidebar (presente em todas as páginas).
document.addEventListener('DOMContentLoaded', function () {
  const historyList = document.getElementById('history-list');
  const historyEmpty = document.getElementById('history-empty');
  const newChatBtn = document.getElementById('new-chat-btn');

  function getCookie(name) {
    let value = null;
    if (document.cookie && document.cookie !== '') {
      for (const c of document.cookie.split(';')) {
        const cookie = c.trim();
        if (cookie.substring(0, name.length + 1) === name + '=') {
          value = decodeURIComponent(cookie.substring(name.length + 1));
          break;
        }
      }
    }
    return value;
  }

  function currentConversationId() {
    const params = new URLSearchParams(window.location.search);
    return params.get('c');
  }

  async function loadHistory() {
    if (!historyList) return;
    try {
      const res = await fetch('/api/conversations/');
      const data = await res.json();
      renderHistory(data.conversations || []);
    } catch (e) {
      // silencioso — histórico indisponível
    }
  }
  window.refreshHistory = loadHistory;

  function renderHistory(conversations) {
    const activeId = currentConversationId();
    historyList.querySelectorAll('.history-item').forEach(el => el.remove());

    if (!conversations.length) {
      if (historyEmpty) historyEmpty.style.display = 'block';
      return;
    }
    if (historyEmpty) historyEmpty.style.display = 'none';

    conversations.forEach(conv => {
      const item = document.createElement('div');
      item.className = 'history-item' + (String(conv.id) === String(activeId) ? ' active' : '');

      const link = document.createElement('a');
      link.href = '/?c=' + conv.id;
      link.className = 'history-item-link';
      link.innerHTML = `
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
        </svg>
        <span class="history-item-title"></span>`;
      link.querySelector('.history-item-title').textContent = conv.title;

      const editBtn = document.createElement('button');
      editBtn.className = 'history-edit-btn';
      editBtn.title = 'Renomear conversa';
      editBtn.innerHTML = `
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M12 20h9"/>
          <path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4z"/>
        </svg>`;
      editBtn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        startRename(item, link, conv);
      });

      const delBtn = document.createElement('button');
      delBtn.className = 'history-delete-btn';
      delBtn.title = 'Excluir conversa';
      delBtn.innerHTML = `
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="3 6 5 6 21 6"/>
          <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>
          <path d="M10 11v6M14 11v6"/>
        </svg>`;
      delBtn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        deleteConversation(conv.id);
      });

      const actions = document.createElement('div');
      actions.className = 'history-actions';
      actions.appendChild(editBtn);
      actions.appendChild(delBtn);

      item.appendChild(link);
      item.appendChild(actions);
      historyList.appendChild(item);
    });
  }

  function startRename(item, link, conv) {
    // Evita abrir dois editores na mesma conversa
    if (item.querySelector('.history-rename-input')) return;

    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'history-rename-input';
    input.value = conv.title || '';
    input.maxLength = 120;

    // Esconde o link e mostra o input no lugar
    link.style.display = 'none';
    item.insertBefore(input, link);
    input.focus();
    input.select();

    let done = false;

    async function commit(save) {
      if (done) return;
      done = true;
      const newTitle = input.value.trim();
      input.remove();
      link.style.display = '';
      if (save && newTitle && newTitle !== conv.title) {
        await renameConversation(conv.id, newTitle);
        loadHistory();
      }
    }

    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); commit(true); }
      else if (e.key === 'Escape') { e.preventDefault(); commit(false); }
    });
    input.addEventListener('blur', () => commit(true));
    input.addEventListener('click', (e) => e.stopPropagation());
  }

  async function renameConversation(id, title) {
    try {
      await fetch(`/api/conversations/${id}/rename/`, {
        method: 'PATCH',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': getCookie('csrftoken'),
        },
        body: JSON.stringify({ title }),
      });
    } catch (e) {
      // silencioso
    }
  }

  async function deleteConversation(id) {
    try {
      await fetch(`/api/conversations/${id}/delete/`, {
        method: 'DELETE',
        headers: { 'X-CSRFToken': getCookie('csrftoken') },
      });
      // Se excluiu a conversa aberta, volta pro chat limpo
      if (String(id) === String(currentConversationId())) {
        window.location.href = '/';
        return;
      }
      loadHistory();
    } catch (e) {
      // silencioso
    }
  }

  if (newChatBtn) {
    newChatBtn.addEventListener('click', () => {
      window.location.href = '/';
    });
  }

  loadHistory();
});
