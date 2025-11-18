const tg = window.Telegram.WebApp;
tg.ready();
tg.expand();

const initData = tg.initData || "";
const root = document.documentElement;

const palette = {
  light: {
    "--app-bg": "linear-gradient(135deg, #edf2ff 0%, #f7f3ff 40%, #fff8f5 100%)",
    "--text-color": "#081631",
    "--surface": "rgba(255, 255, 255, 0.96)",
    "--surface-muted": "rgba(255, 255, 255, 0.9)",
    "--hero-bg": "linear-gradient(135deg, #11153c, #3146c6)",
    "--hero-text": "#fdfbff",
    "--hero-subtitle": "rgba(255, 255, 255, 0.85)",
    "--muted-text": "rgba(8, 22, 49, 0.6)",
    "--outline": "rgba(99, 102, 241, 0.2)",
    "--ghost-bg": "rgba(255, 255, 255, 0.92)",
    "--ghost-border": "rgba(99, 102, 241, 0.25)",
    "--badge-bg": "rgba(37, 99, 235, 0.15)",
    "--badge-text": "#1d4ed8",
    "--accent-color": "#6366f1",
    "--accent-contrast": "#ffffff",
  },
  dark: {
    "--app-bg": "linear-gradient(135deg, #050710 0%, #0f162c 100%)",
    "--text-color": "#e6eaff",
    "--surface": "rgba(19, 24, 42, 0.92)",
    "--surface-muted": "rgba(27, 33, 56, 0.9)",
    "--hero-bg": "linear-gradient(135deg, #1a2141, #1f2d62)",
    "--hero-text": "#f6f8ff",
    "--hero-subtitle": "rgba(246, 248, 255, 0.8)",
    "--muted-text": "rgba(210, 220, 255, 0.7)",
    "--outline": "rgba(99, 102, 241, 0.45)",
    "--ghost-bg": "rgba(33, 41, 68, 0.95)",
    "--ghost-border": "rgba(99, 102, 241, 0.5)",
    "--badge-bg": "rgba(91, 103, 255, 0.3)",
    "--badge-text": "#cfd8ff",
    "--accent-color": "#7c83ff",
    "--accent-contrast": "#0a1024",
  },
};

function applyPalette() {
  const scheme = tg.colorScheme || "light";
  const current = palette[scheme] || palette.light;
  Object.entries(current).forEach(([token, value]) => root.style.setProperty(token, value));
}

applyPalette();
tg.onEvent("themeChanged", applyPalette);

const state = {
  me: null,
  referrers: [],
  leaderboard: [],
  reminders: [],
  baseAdmins: [],
  extraAdmins: [],
  approvedChats: { base: [], entries: [] },
};

let reminderMediaPath = null;

const tabButtons = document.querySelectorAll(".tab-button");
const tabSections = document.querySelectorAll(".tab-section");
tabButtons.forEach((button) =>
  button.addEventListener("click", () => activateTab(button.dataset.tabTarget))
);

function activateTab(target) {
  tabButtons.forEach((btn) => btn.classList.toggle("is-active", btn.dataset.tabTarget === target));
  tabSections.forEach((section) =>
    section.classList.toggle("is-active", section.dataset.tab === target)
  );
}

const summaryCardsEl = document.getElementById("summary-cards");
const leaderboardEl = document.getElementById("leaderboard");
const referrerListEl = document.getElementById("referrer-list");
const reminderListEl = document.getElementById("reminder-list");
const addForm = document.getElementById("add-form");
const addReferrerSelect = document.getElementById("add-referrer");
const addNamesInput = document.getElementById("add-names");
const removeForm = document.getElementById("remove-form");
const removeReferrerSelect = document.getElementById("remove-referrer");
const removeSelect = document.getElementById("remove-select");
const removeReasonInput = document.getElementById("remove-reason");
const removeDateInput = document.getElementById("remove-date");
const cpmForm = document.getElementById("cpm-form");
const cpmReferrerSelect = document.getElementById("cpm-referrer");
const cpmValueInput = document.getElementById("cpm-value");
const announceForm = document.getElementById("announce-form");
const announceReferrerSelect = document.getElementById("announce-referrer");
const exportBtn = document.getElementById("export-btn");
const reminderForm = document.getElementById("reminder-form");
const reminderModeRadios = document.querySelectorAll("input[name='rem-mode']");
const reminderModeSections = document.querySelectorAll("[data-rem-mode]");
const reminderTextInput = document.getElementById("rem-text");
const reminderOnceInput = document.getElementById("rem-once");
const reminderSendNowCheckbox = document.getElementById("rem-send-now");
const reminderScheduleDayInputs = document.querySelectorAll("#rem-days input");
const reminderScheduleTimeInput = document.getElementById("rem-schedule-time");
const reminderPreviewBtn = document.getElementById("reminder-preview");
const reminderBoldBtn = document.getElementById("rem-bold");
const reminderItalicBtn = document.getElementById("rem-italic");
const reminderPhotoBtn = document.getElementById("rem-photo-btn");
const reminderPhotoInput = document.getElementById("rem-photo");
const reminderPhotoRemoveBtn = document.getElementById("rem-photo-remove");
const reminderPhotoStatus = document.getElementById("rem-photo-status");
const refreshBtn = document.getElementById("refresh-btn");
const createReferrerForm = document.getElementById("ref-form");
const refNameInput = document.getElementById("ref-name");
const refCpmInput = document.getElementById("ref-cpm");
const accessPanel = document.getElementById("access-panel");
const baseAdminList = document.getElementById("base-admin-list");
const extraAdminList = document.getElementById("extra-admin-list");
const adminForm = document.getElementById("admin-form");
const adminUsernameInput = document.getElementById("admin-username");
const chatListEl = document.getElementById("chat-list");
const chatForm = document.getElementById("chat-form");
const chatIdInput = document.getElementById("chat-id");
const chatTitleInput = document.getElementById("chat-title");

const modalLayer = document.getElementById("modal-layer");
const modals = modalLayer.querySelectorAll(".modal");
const modalTriggers = document.querySelectorAll("[data-modal-target]");
const closeButtons = modalLayer.querySelectorAll(".close-modal");
let activeModal = null;

modalTriggers.forEach((trigger) =>
  trigger.addEventListener("click", () => openModal(trigger.dataset.modalTarget))
);
closeButtons.forEach((btn) => btn.addEventListener("click", closeModal));
modalLayer.addEventListener("click", (event) => {
  if (event.target === modalLayer) closeModal();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeModal();
});

function openModal(name) {
  modalLayer.classList.add("is-visible");
  modals.forEach((modal) => {
    const isTarget = modal.dataset.modal === name;
    modal.classList.toggle("is-open", isTarget);
    if (isTarget) activeModal = modal;
  });
  if (name === "reminder") {
    updateReminderFields();
  }
  if (name === "remove-referrals") {
    updateRemoveDropdown();
  }
}

function closeModal() {
  const previousModal = activeModal;
  modalLayer.classList.remove("is-visible");
  modals.forEach((modal) => modal.classList.remove("is-open"));
  activeModal = null;
  if (previousModal?.dataset?.modal === "reminder") {
    resetReminderMedia();
  }
}

refreshBtn.addEventListener("click", () => bootstrap(true));
reminderModeRadios.forEach((radio) => radio.addEventListener("change", updateReminderFields));
updateReminderFields();

async function request(path, options = {}) {
  const res = await fetch(`/api${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-Telegram-Init-Data": initData,
      ...(options.headers || {}),
    },
  });
  if (!res.ok) {
    const payload = await res.json().catch(() => ({}));
    throw new Error(payload.error || "Request failed");
  }
  return res.json();
}

async function fetchFile(path) {
  const res = await fetch(`/api${path}`, {
    headers: { "X-Telegram-Init-Data": initData },
  });
  if (!res.ok) {
    const payload = await res.json().catch(() => ({}));
    throw new Error(payload.error || "Request failed");
  }
  return res.blob();
}

async function bootstrap(showToast = false) {
  try {
    state.me = await request("/me");
    if (accessPanel) {
      accessPanel.hidden = !state.me.is_primary;
    }
    if (!state.me.is_primary) {
      document.querySelectorAll('[data-modal-target="add-referrer"]').forEach((btn) => {
        btn.style.display = "none";
      });
    }
    await Promise.all([refreshReferrers(), refreshReminders()]);
    if (state.me.is_primary) {
      await refreshAccessData();
    }
    if (showToast) tg.showPopup({ title: "Synced", message: "Data refreshed." });
  } catch (err) {
    tg.showAlert(err.message);
  }
}

async function refreshReferrers() {
  const data = await request("/referrers");
  state.referrers = data.referrers;
  state.leaderboard = data.leaderboard;
  renderSummary(data.summary);
  renderLeaderboard();
  renderReferrerCards();
  syncSelects();
}

async function refreshReminders() {
  const data = await request("/reminders");
  state.reminders = data.reminders || [];
  renderReminders();
}

async function refreshAccessData() {
  const [admins, chats] = await Promise.all([request("/admins"), request("/approved_chats")]);
  state.baseAdmins = admins.base || [];
  state.extraAdmins = admins.extras || [];
  state.approvedChats = chats || { base: [], entries: [] };
  renderAdmins();
  renderChats();
}

function renderSummary(summary) {
  summaryCardsEl.innerHTML = `
    <div class="summary-card">
      <p>Total referrals</p>
      <h3>${summary.total_referrals}</h3>
      <p>${state.leaderboard?.[0] ? `Leader: ${state.leaderboard[0].name}` : "No leader yet"}</p>
    </div>
    <div class="summary-card">
      <p>Cash to date</p>
      <h3>$${summary.total_cash.toLocaleString()}</h3>
      <p>$${summary.program.cash_per_referral} per qualified driver</p>
    </div>
    <div class="summary-card">
      <p>Program rule</p>
      <h3>+${summary.program.step_bonus} CPM</h3>
      <p>every ${summary.program.step} referrals</p>
    </div>
  `;
}

function renderLeaderboard() {
  leaderboardEl.innerHTML = "";
  if (!state.leaderboard.length) {
    leaderboardEl.innerHTML = "<p>No referrals yet.</p>";
    return;
  }
  state.leaderboard.forEach((row, idx) => {
    const card = document.createElement("div");
    card.className = "leaderboard-card";
    card.innerHTML = `
      <div>
        <strong>${idx + 1}. ${row.name}</strong>
        <div class="meta">${row.count} referrals</div>
      </div>
      <span class="badge">+${row.bonus} CPM</span>
    `;
    leaderboardEl.appendChild(card);
  });
}

function renderReferrerCards() {
  referrerListEl.innerHTML = "";
  if (!state.referrers.length) {
    referrerListEl.innerHTML = "<p>No referrers yet.</p>";
    return;
  }
  const template = document.getElementById("referrer-card-template");
  state.referrers.forEach((ref) => {
    const fragment = template.content.cloneNode(true);
    fragment.querySelector("h3").textContent = ref.name;
    fragment.querySelector(".meta").textContent = `${ref.referral_count} referrals · Base ${
      ref.base_cpm
    } → ${ref.new_cpm}`;
    fragment.querySelector(".badge").textContent = `+${ref.bonus_cpm} CPM`;
    const list = fragment.querySelector(".referral-list");
    list.innerHTML = "";
    if (!ref.referrals.length) {
      const li = document.createElement("li");
      li.textContent = "No referrals yet.";
      list.appendChild(li);
    } else {
      ref.referrals.forEach((entry) => {
        const li = document.createElement("li");
        const created = entry.created_at.slice(0, 10);
        li.innerHTML = `<span>${entry.name} · ${created}</span>`;
        if (entry.is_removed) {
          li.classList.add("removed");
          const details = [];
          if (entry.removed_at) details.push(entry.removed_at.slice(0, 10));
          if (entry.removed_reason) details.push(entry.removed_reason);
          if (details.length) {
            const small = document.createElement("small");
            small.textContent = `Removed ${details.join(" • ")}`;
            li.appendChild(small);
          }
        }
        list.appendChild(li);
      });
    }
    referrerListEl.appendChild(fragment);
  });
}

function renderReminders() {
  reminderListEl.innerHTML = "";
  if (!state.reminders.length) {
    reminderListEl.innerHTML = "<p>No reminders yet.</p>";
    return;
  }
  state.reminders.forEach((rem) => {
    const row = document.createElement("div");
    row.className = "reminder-item";
    row.innerHTML = `
      <div class="reminder-meta">
        <h4>${rem.text}</h4>
        <p>${rem.schedule}${rem.has_media ? " · 📷" : ""}</p>
      </div>
      <div class="reminder-actions">
        <button data-action="toggle" data-id="${rem.id}" data-active="${rem.active}">
          ${rem.active ? "Disable" : "Enable"}
        </button>
        <button data-action="delete" data-id="${rem.id}" class="danger">Delete</button>
      </div>
    `;
    reminderListEl.appendChild(row);
  });
}

function renderAdmins() {
  if (!baseAdminList || !extraAdminList) return;
  baseAdminList.innerHTML = state.baseAdmins
    .map(
      (admin) =>
        `<li>${admin.username ? "@"+admin.username : admin.id}${admin.is_primary ? " (Primary)" : ""}</li>`
    )
    .join("");
  extraAdminList.innerHTML = state.extraAdmins
    .map(
      (admin) =>
        `<li><span>${admin.username ? "@"+admin.username : admin.user_id}</span><button data-admin-id="${admin.user_id}">Remove</button></li>`
    )
    .join("");
}

function renderChats() {
  if (!chatListEl) return;
  const base = (state.approvedChats.base || [])
    .map((chat) => `<li>${chat.title || chat.chat_id}<small>Configured</small></li>`)
    .join("");
  const entries = (state.approvedChats.entries || [])
    .map(
      (chat) =>
        `<li><span>${chat.title || chat.chat_id}</span><button data-chat-id="${chat.chat_id}">Remove</button></li>`
    )
    .join("");
  chatListEl.innerHTML = base + entries;
}

function syncSelects() {
  const selects = [
    addReferrerSelect,
    removeReferrerSelect,
    cpmReferrerSelect,
    announceReferrerSelect,
  ];
  selects.forEach((select) => {
    const current = select.value;
    select.innerHTML = state.referrers
      .map((ref) => `<option value="${ref.id}">${ref.name}</option>`)
      .join("");
    if (state.referrers.some((ref) => String(ref.id) === current)) {
      select.value = current;
    }
  });
  if (state.referrers.length) {
    const first = state.referrers[0];
    cpmValueInput.value = first.base_cpm;
  }
  updateRemoveDropdown();
}

if (removeReferrerSelect) {
  removeReferrerSelect.addEventListener("change", updateRemoveDropdown);
}

function updateRemoveDropdown() {
  if (!removeSelect || !removeReferrerSelect) return;
  const refId = removeReferrerSelect.value;
  const ref = state.referrers.find((r) => String(r.id) === refId);
  const options = (ref?.referrals || []).filter((entry) => !entry.is_removed);
  removeSelect.innerHTML = options
    .map(
      (entry) =>
        `<option value="${entry.id}">${entry.name} · ${entry.created_at.slice(0, 10)}</option>`
    )
    .join("");
}

addForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const referrerId = addReferrerSelect.value;
  const names = addNamesInput.value.trim();
  if (!names) return tg.showAlert("Add at least one name.");
  const confirmed = await confirmAction(
    `Add ${names.split(/[,\n]+/).filter(Boolean).length} referral(s)?`
  );
  if (!confirmed) return;
  try {
    await request(`/referrers/${referrerId}/referrals`, {
      method: "POST",
      body: JSON.stringify({ names }),
    });
    addNamesInput.value = "";
    closeModal();
    tg.showPopup({ title: "Done", message: "Referrals added." });
    await refreshReferrers();
  } catch (err) {
    tg.showAlert(err.message);
  }
});

removeForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!removeSelect) return;
  const referrerId = removeReferrerSelect.value;
  const selected = Array.from(removeSelect.selectedOptions).map((opt) => Number(opt.value));
  if (!selected.length) return tg.showAlert("Select at least one driver.");
  const confirmed = await confirmAction("Remove the selected referrals?");
  if (!confirmed) return;
  try {
    await request(`/referrers/${referrerId}/referrals/remove`, {
      method: "POST",
      body: JSON.stringify({
        referral_ids: selected,
        reason: removeReasonInput.value,
        removed_at: removeDateInput.value || undefined,
      }),
    });
    removeReasonInput.value = "";
    removeDateInput.value = "";
    removeSelect.innerHTML = "";
    closeModal();
    tg.showPopup({ title: "Removed", message: "Referrals removed." });
    await refreshReferrers();
  } catch (err) {
    tg.showAlert(err.message);
  }
});

cpmForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const referrerId = cpmReferrerSelect.value;
  const baseCpm = Number(cpmValueInput.value);
  if (Number.isNaN(baseCpm)) return tg.showAlert("Enter a valid CPM.");
  try {
    await request(`/referrers/${referrerId}`, {
      method: "PATCH",
      body: JSON.stringify({ base_cpm: baseCpm }),
    });
    closeModal();
    tg.showPopup({ title: "Updated", message: "CPM updated." });
    await refreshReferrers();
  } catch (err) {
    tg.showAlert(err.message);
  }
});

announceForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const referrerId = announceReferrerSelect.value;
  const confirmed = await confirmAction("Broadcast this announcement to every chat?");
  if (!confirmed) return;
  try {
    await request(`/referrers/${referrerId}/announce`, { method: "POST" });
    closeModal();
    tg.showPopup({ title: "Sent", message: "Announcement broadcast." });
  } catch (err) {
    tg.showAlert(err.message);
  }
});

exportBtn.addEventListener("click", async () => {
  try {
    const blob = await fetchFile("/referrals/export");
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "referrals.xlsx";
    anchor.click();
    URL.revokeObjectURL(url);
  } catch (err) {
    tg.showAlert(err.message);
  }
});

if (reminderPreviewBtn) {
  reminderPreviewBtn.addEventListener("click", async () => {
    const text = reminderTextInput.value.trim();
    if (!text) return tg.showAlert("Provide reminder text first.");
    const mode = currentReminderMode();
    const payload = { text, mode };
    if (reminderMediaPath) {
      payload.media_path = reminderMediaPath;
    }
    if (mode === "schedule") {
      const days = Array.from(reminderScheduleDayInputs)
        .filter((input) => input.checked)
        .map((input) => Number(input.value));
      if (!days.length || !reminderScheduleTimeInput.value) {
        return tg.showAlert("Choose days and time first.");
      }
      payload.days = days;
      payload.time_of_day = reminderScheduleTimeInput.value;
    }
    try {
      await request("/reminders/preview", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      tg.showPopup({ title: "Preview sent", message: "Reminder preview sent to this chat." });
    } catch (err) {
      tg.showAlert(err.message);
    }
  });
}

reminderForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const payload = buildReminderPayload();
  if (!payload) return;
  try {
    await request("/reminders", { method: "POST", body: JSON.stringify(payload) });
    reminderTextInput.value = "";
    reminderOnceInput.value = "";
    reminderScheduleTimeInput.value = "";
    reminderSendNowCheckbox.checked = false;
    reminderScheduleDayInputs.forEach((input) => (input.checked = false));
    reminderMediaPath = null;
    if (reminderPhotoInput) reminderPhotoInput.value = "";
    if (reminderPhotoStatus) reminderPhotoStatus.textContent = "No photo selected.";
    if (reminderPhotoRemoveBtn) reminderPhotoRemoveBtn.hidden = true;
    closeModal();
    tg.showPopup({ title: "Saved", message: "Reminder created." });
    await refreshReminders();
  } catch (err) {
    tg.showAlert(err.message);
  }
});

createReferrerForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const name = refNameInput.value.trim();
  const baseCpm = Number(refCpmInput.value);
  if (!name) return tg.showAlert("Name is required.");
  if (Number.isNaN(baseCpm)) return tg.showAlert("Enter a valid base CPM.");
  try {
    await request("/referrers", { method: "POST", body: JSON.stringify({ name, base_cpm: baseCpm }) });
    refNameInput.value = "";
    refCpmInput.value = "";
    closeModal();
    tg.showPopup({ title: "Saved", message: "Referrer created." });
    await refreshReferrers();
  } catch (err) {
    tg.showAlert(err.message);
  }
});

if (adminForm) {
  adminForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const username = adminUsernameInput.value.trim();
    if (!username) return;
    try {
      await request("/admins", { method: "POST", body: JSON.stringify({ username }) });
      adminUsernameInput.value = "";
      await refreshAccessData();
    } catch (err) {
      tg.showAlert(err.message);
    }
  });
}

if (extraAdminList) {
  extraAdminList.addEventListener("click", async (event) => {
    const button = event.target.closest("button[data-admin-id]");
    if (!button) return;
    const adminId = button.dataset.adminId;
    const confirmed = await confirmAction("Remove this admin?");
    if (!confirmed) return;
    try {
      await request(`/admins/${adminId}`, { method: "DELETE" });
      await refreshAccessData();
    } catch (err) {
      tg.showAlert(err.message);
    }
  });
}

if (chatForm) {
  chatForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const chatId = chatIdInput.value.trim();
    if (!chatId) return;
    try {
      await request("/approved_chats", {
        method: "POST",
        body: JSON.stringify({ chat_id: chatId, title: chatTitleInput.value }),
      });
      chatIdInput.value = "";
      chatTitleInput.value = "";
      await refreshAccessData();
    } catch (err) {
      tg.showAlert(err.message);
    }
  });
}

if (chatListEl) {
  chatListEl.addEventListener("click", async (event) => {
    const button = event.target.closest("button[data-chat-id]");
    if (!button) return;
    const chatId = button.dataset.chatId;
    const confirmed = await confirmAction("Remove this chat?");
    if (!confirmed) return;
    try {
      await request(`/approved_chats/${chatId}`, { method: "DELETE" });
      await refreshAccessData();
    } catch (err) {
      tg.showAlert(err.message);
    }
  });
}

reminderListEl.addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  const id = button.dataset.id;
  const action = button.dataset.action;
  try {
    if (action === "toggle") {
      const active = button.dataset.active === "true";
      await request(`/reminders/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ active: !active }),
      });
    } else if (action === "delete") {
      const confirmed = await confirmAction("Delete this reminder?");
      if (!confirmed) return;
      await request(`/reminders/${id}`, { method: "DELETE" });
    }
    await refreshReminders();
  } catch (err) {
    tg.showAlert(err.message);
  }
});

function currentReminderMode() {
  const checked = document.querySelector('input[name="rem-mode"]:checked');
  return checked ? checked.value : "once";
}

function buildReminderPayload() {
  const mode = currentReminderMode();
  const text = reminderTextInput.value.trim();
  if (!text) {
    tg.showAlert("Reminder text is required.");
    return null;
  }
  const payload = { text, mode, media_path: reminderMediaPath };
  if (mode === "once") {
    if (reminderSendNowCheckbox.checked) {
      payload.send_now = true;
      return payload;
    }
    if (!reminderOnceInput.value) {
      tg.showAlert("Pick the send date and time.");
      return null;
    }
    payload.run_at = reminderOnceInput.value;
    return payload;
  }
  const days = Array.from(reminderScheduleDayInputs)
    .filter((input) => input.checked)
    .map((input) => Number(input.value));
  if (!days.length) {
    tg.showAlert("Select at least one day.");
    return null;
  }
  if (!reminderScheduleTimeInput.value) {
    tg.showAlert("Pick the time of day.");
    return null;
  }
  payload.days = days;
  payload.time_of_day = reminderScheduleTimeInput.value;
  return payload;
}

function updateReminderFields() {
  const mode = currentReminderMode();
  reminderModeSections.forEach((section) => {
    section.style.display = section.dataset.remMode === mode ? "block" : "none";
  });
}

function confirmAction(message) {
  return new Promise((resolve) => {
    if (tg.showConfirm) {
      tg.showConfirm(message, (ok) => resolve(ok));
    } else {
      resolve(window.confirm(message));
    }
  });
}

function applyTextFormatting(textarea, tag) {
  if (!textarea) return;
  const start = textarea.selectionStart;
  const end = textarea.selectionEnd;
  const value = textarea.value;
  if (start === undefined || end === undefined) {
    textarea.value = `${value}<${tag}></${tag}>`;
    textarea.focus();
    return;
  }
  const selected = value.slice(start, end) || "text";
  const formatted = `<${tag}>${selected}</${tag}>`;
  textarea.value = value.slice(0, start) + formatted + value.slice(end);
  textarea.focus();
  const cursor = start + formatted.length;
  textarea.setSelectionRange(cursor, cursor);
}

async function uploadReminderPhoto(file) {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch("/api/uploads/reminder_media", {
    method: "POST",
    body: form,
    headers: {
      "X-Telegram-Init-Data": initData,
    },
  });
  if (!res.ok) {
    const payload = await res.json().catch(() => ({}));
    throw new Error(payload.error || "Upload failed");
  }
  const payload = await res.json();
  return payload.path;
}

function resetReminderMedia() {
  reminderMediaPath = null;
  if (reminderPhotoInput) reminderPhotoInput.value = "";
  if (reminderPhotoStatus) reminderPhotoStatus.textContent = "No photo selected.";
  if (reminderPhotoRemoveBtn) reminderPhotoRemoveBtn.hidden = true;
}

bootstrap();
if (reminderBoldBtn) {
  reminderBoldBtn.addEventListener("click", () => applyTextFormatting(reminderTextInput, "b"));
}
if (reminderItalicBtn) {
  reminderItalicBtn.addEventListener("click", () => applyTextFormatting(reminderTextInput, "i"));
}
if (reminderPhotoBtn && reminderPhotoInput) {
  reminderPhotoBtn.addEventListener("click", () => reminderPhotoInput.click());
  reminderPhotoInput.addEventListener("change", async () => {
    const file = reminderPhotoInput.files?.[0];
    if (!file) return;
    try {
      const mediaPath = await uploadReminderPhoto(file);
      reminderMediaPath = mediaPath;
      if (reminderPhotoStatus) reminderPhotoStatus.textContent = `Photo attached (${file.name})`;
      if (reminderPhotoRemoveBtn) reminderPhotoRemoveBtn.hidden = false;
    } catch (err) {
      tg.showAlert(err.message);
      reminderPhotoInput.value = "";
    }
  });
}
if (reminderPhotoRemoveBtn) {
  reminderPhotoRemoveBtn.addEventListener("click", () => {
    reminderMediaPath = null;
    if (reminderPhotoInput) reminderPhotoInput.value = "";
    if (reminderPhotoStatus) reminderPhotoStatus.textContent = "No photo selected.";
    reminderPhotoRemoveBtn.hidden = true;
  });
}
