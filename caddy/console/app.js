"use strict";

const loginView = document.getElementById("login-view");
const appView = document.getElementById("app-view");
const loginForm = document.getElementById("login-form");
const loginError = document.getElementById("login-error");
const whoami = document.getElementById("whoami");
const logoutBtn = document.getElementById("logout-btn");
const newConversationBtn = document.getElementById("new-conversation-btn");
const conversationListEl = document.getElementById("conversation-list");
const emptyState = document.getElementById("empty-state");
const conversationPanel = document.getElementById("conversation-panel");
const messagesEl = document.getElementById("messages");
const sendForm = document.getElementById("send-form");
const sendInput = document.getElementById("send-input");

const settingTitle = document.getElementById("setting-title");
const settingModel = document.getElementById("setting-model");
const settingSystemPrompt = document.getElementById("setting-system-prompt");
const settingTemperature = document.getElementById("setting-temperature");
const settingTopP = document.getElementById("setting-top-p");
const settingMaxTokens = document.getElementById("setting-max-tokens");
const saveSettingsBtn = document.getElementById("save-settings-btn");
const settingsSaved = document.getElementById("settings-saved");

let currentConversationId = null;
let conversations = [];

async function api(path, options = {}) {
  const res = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  if (!res.ok) {
    let message = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      message = body?.error?.message || message;
    } catch {
      /* ignore parse error */
    }
    throw new Error(message);
  }
  if (res.status === 204) return null;
  return res.json();
}

function showLogin() {
  loginView.hidden = false;
  appView.hidden = true;
}

function showApp() {
  loginView.hidden = true;
  appView.hidden = false;
}

async function checkAuthAndInit() {
  try {
    const me = await api("/api/auth/me");
    whoami.textContent = me.email;
    showApp();
    await Promise.all([loadModels(), loadConversations()]);
  } catch {
    showLogin();
  }
}

loginForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  loginError.hidden = true;
  const email = document.getElementById("login-email").value;
  const password = document.getElementById("login-password").value;
  try {
    await api("/api/auth/login", { method: "POST", body: JSON.stringify({ email, password }) });
    loginForm.reset();
    await checkAuthAndInit();
  } catch (err) {
    loginError.textContent = err.message === "HTTP 401" ? "メールアドレスまたはパスワードが違います" : err.message;
    loginError.hidden = false;
  }
});

logoutBtn.addEventListener("click", async () => {
  await api("/api/auth/logout", { method: "POST" }).catch(() => {});
  currentConversationId = null;
  showLogin();
});

async function loadModels() {
  const res = await api("/api/v1/models");
  settingModel.innerHTML = "";
  for (const m of res.data) {
    const opt = document.createElement("option");
    opt.value = m.id;
    opt.textContent = m.id;
    settingModel.appendChild(opt);
  }
}

async function loadConversations() {
  conversations = await api("/api/conversations");
  renderConversationList();
  if (conversations.length === 0) {
    currentConversationId = null;
    emptyState.hidden = false;
    conversationPanel.hidden = true;
  } else if (!conversations.some((c) => c.id === currentConversationId)) {
    await selectConversation(conversations[0].id);
  }
}

function renderConversationList() {
  conversationListEl.innerHTML = "";
  for (const c of conversations) {
    const li = document.createElement("li");
    li.textContent = c.title || `(無題 #${c.id})`;
    li.dataset.id = String(c.id);
    if (c.id === currentConversationId) li.classList.add("active");
    li.addEventListener("click", () => selectConversation(c.id));
    conversationListEl.appendChild(li);
  }
}

newConversationBtn.addEventListener("click", async () => {
  const defaultModel = settingModel.options[0]?.value || null;
  const created = await api("/api/conversations", {
    method: "POST",
    body: JSON.stringify({ title: "新しい会話", model: defaultModel }),
  });
  await loadConversations();
  await selectConversation(created.id);
});

async function selectConversation(id) {
  currentConversationId = id;
  emptyState.hidden = true;
  conversationPanel.hidden = false;
  renderConversationList();

  const conv = await api(`/api/conversations/${id}`);
  settingTitle.value = conv.title;
  settingModel.value = conv.model || "";
  settingSystemPrompt.value = conv.system_prompt;
  settingTemperature.value = conv.temperature;
  settingTopP.value = conv.top_p;
  settingMaxTokens.value = conv.max_tokens ?? "";

  const messages = await api(`/api/conversations/${id}/messages`);
  messagesEl.innerHTML = "";
  for (const m of messages) {
    if (m.role === "system") continue;
    appendBubble(m.role, m.content);
  }
  scrollMessagesToBottom();
}

saveSettingsBtn.addEventListener("click", async () => {
  if (currentConversationId === null) return;
  const maxTokensValue = settingMaxTokens.value.trim();
  await api(`/api/conversations/${currentConversationId}`, {
    method: "PATCH",
    body: JSON.stringify({
      title: settingTitle.value,
      model: settingModel.value || null,
      system_prompt: settingSystemPrompt.value,
      temperature: Number(settingTemperature.value),
      top_p: Number(settingTopP.value),
      max_tokens: maxTokensValue === "" ? null : Number(maxTokensValue),
    }),
  });
  await loadConversations();
  settingsSaved.hidden = false;
  setTimeout(() => (settingsSaved.hidden = true), 1500);
});

function appendBubble(role, text) {
  const div = document.createElement("div");
  div.className = `bubble ${role === "user" ? "user" : "assistant"}`;
  div.textContent = text;
  messagesEl.appendChild(div);
  return div;
}

function scrollMessagesToBottom() {
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

sendForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = sendInput.value.trim();
  if (!text || currentConversationId === null) return;
  sendInput.value = "";
  sendInput.disabled = true;

  appendBubble("user", text);
  const assistantEl = appendBubble("assistant", "");
  scrollMessagesToBottom();

  try {
    const res = await fetch(`/api/conversations/${currentConversationId}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content: text }),
    });

    if (!res.ok || !res.body) {
      let message = `HTTP ${res.status}`;
      try {
        message = (await res.json())?.error?.message || message;
      } catch {
        /* ignore */
      }
      assistantEl.textContent = `[エラー] ${message}`;
      return;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let full = "";

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";
      for (const rawLine of lines) {
        const line = rawLine.trim();
        if (!line.startsWith("data: ")) continue;
        const payload = line.slice(6);
        if (payload === "[DONE]") continue;
        try {
          const obj = JSON.parse(payload);
          const delta = obj.choices?.[0]?.delta?.content;
          if (delta) {
            full += delta;
            assistantEl.textContent = full;
            scrollMessagesToBottom();
          }
        } catch {
          /* 断片的なJSONは無視 */
        }
      }
    }
  } catch (err) {
    assistantEl.textContent = `[エラー] ${err.message}`;
  } finally {
    sendInput.disabled = false;
    sendInput.focus();
    loadConversations();
  }
});

sendInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendForm.requestSubmit();
  }
});

checkAuthAndInit();
