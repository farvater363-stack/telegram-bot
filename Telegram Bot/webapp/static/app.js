/* =========================================================
 * Spotlight — referrals control
 *
 * One composer at the top, one feed of active/upcoming items,
 * one people list sorted by referrals. No tabs, no modals.
 * Safe by default: every render uses textContent / createElement.
 * Demo mode: append ?demo to render against canned data.
 * ========================================================= */
"use strict";

const tg = window.Telegram?.WebApp || {
  ready: () => {},
  expand: () => {},
  colorScheme: "light",
  onEvent: () => {},
  offEvent: () => {},
  showAlert: (m) => alert(m),
  showPopup: (opts) => alert(opts.message || opts.title || ""),
  showConfirm: (m, cb) => cb(window.confirm(m)),
  HapticFeedback: { notificationOccurred: () => {}, impactOccurred: () => {} },
  initData: "",
  themeParams: {},
  MainButton: {
    setParams: () => {},
    onClick: () => {},
    offClick: () => {},
    show: () => {},
    hide: () => {},
    showProgress: () => {},
    hideProgress: () => {},
    setText: () => {},
  },
};
tg.ready();
tg.expand();

const initData = tg.initData || "";
const DEMO_MODE = new URLSearchParams(window.location.search).has("demo");

/* ---------- Theme ---------- */
function applyTheme() {
  document.documentElement.dataset.theme =
    tg.colorScheme === "dark" ? "dark" : "light";
}
applyTheme();
tg.onEvent?.("themeChanged", applyTheme);

/* ---------- Date in masthead ---------- */
(() => {
  const masthead = document.getElementById("masthead-date");
  if (!masthead) return;
  const d = new Date();
  const fmt = new Intl.DateTimeFormat(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
  });
  masthead.textContent = fmt.format(d);
})();

/* ---------- App state ---------- */
const state = {
  me: null,
  referrers: [],
  leaderboard: [],
  summary: null,
  reminders: [],
  schedule: { days: [], time_of_day: "" },
  baseAdmins: [],
  extraAdmins: [],
  approvedChats: { base: [], entries: [] },
  expandedReferrer: null,
  searchQuery: "",
  composeMediaPath: null,
};

const compose = {
  mode: "spotlight", // 'spotlight' | 'custom'
  when: "now",       // 'now' | 'once' | 'repeat'
};

/* ---------- DOM refs ---------- */
const el = (id) => document.getElementById(id);

const refs = {
  meBadge: el("me-badge"),
  settingsBtn: el("settings-btn"),
  drawer: el("settings-drawer"),

  // Compose
  composeEl: el("compose"),
  composeModeRadios: document.querySelectorAll("input[name='compose-mode']"),
  composeWhenRadios: document.querySelectorAll("input[name='compose-when']"),
  composeBodies: document.querySelectorAll(".compose-body"),
  composeWhenDetails: document.querySelectorAll(".compose-when-detail"),
  composeWhenOnceLabel: document.querySelector("[data-when-option='once']"),
  composeReferrer: el("compose-referrer"),
  composePreviewWrap: el("compose-preview-wrap"),
  composePreview: el("compose-preview"),
  composeSpotEmpty: el("compose-spotlight-empty"),
  composeText: el("compose-text"),
  composeBold: el("compose-bold"),
  composeItalic: el("compose-italic"),
  composePhotoBtn: el("compose-photo-btn"),
  composePhoto: el("compose-photo"),
  composePhotoRemove: el("compose-photo-remove"),
  composePhotoStatus: el("compose-photo-status"),
  composeOnce: el("compose-once"),
  composeDays: el("compose-days"),
  composeRepeatTime: el("compose-repeat-time"),
  composeSkipInactive: el("compose-skip-inactive"),
  composePreviewSelf: el("compose-preview-self"),
  composeSend: el("compose-send"),
  composeLede: el("compose-lede"),

  // Feed
  feedList: el("feed-list"),

  // People
  peopleList: el("people-list"),
  peopleSearch: el("people-search"),
  addReferrerToggle: el("add-referrer-toggle"),
  addReferrerForm: el("add-referrer-form"),
  refName: el("ref-name"),
  refCpm: el("ref-cpm"),
  addReferrerCancel: el("add-referrer-cancel"),

  // Templates
  personTpl: el("people-row-template"),
  feedItemTpl: el("feed-item-template"),

  // Drawer
  exportBtn: el("export-btn"),
  accessSection: el("access-section"),
  chatsSection: el("chats-section"),
  baseAdminList: el("base-admin-list"),
  extraAdminList: el("extra-admin-list"),
  adminForm: el("admin-form"),
  adminUsername: el("admin-username"),
  chatList: el("chat-list"),
  chatForm: el("chat-form"),
  chatId: el("chat-id"),
  chatTitle: el("chat-title"),
};

/* =========================================================
 * Network
 * ========================================================= */

async function api(path, options = {}) {
  if (DEMO_MODE) return demoApi(path, options);
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
    throw new Error(payload.error || `Request failed (${res.status})`);
  }
  return res.json();
}

async function apiBlob(path) {
  if (DEMO_MODE) {
    return new Blob(["demo"], { type: "application/octet-stream" });
  }
  const res = await fetch(`/api${path}`, {
    headers: { "X-Telegram-Init-Data": initData },
  });
  if (!res.ok) {
    const payload = await res.json().catch(() => ({}));
    throw new Error(payload.error || "Request failed");
  }
  return res.blob();
}

function notify(title, message, type = "ok") {
  tg.HapticFeedback?.notificationOccurred?.(type === "ok" ? "success" : "error");
  if (tg.showPopup) tg.showPopup({ title, message: message || "" });
  else tg.showAlert(`${title}\n${message || ""}`);
}

function fail(err) {
  tg.HapticFeedback?.notificationOccurred?.("error");
  tg.showAlert(err?.message || String(err));
}

function confirmAction(message) {
  return new Promise((resolve) => {
    if (tg.showConfirm) tg.showConfirm(message, (ok) => resolve(Boolean(ok)));
    else resolve(window.confirm(message));
  });
}

/* =========================================================
 * Bootstrap
 * ========================================================= */

async function bootstrap() {
  try {
    state.me = await api("/me");
    if (refs.meBadge) {
      const u = state.me.user || {};
      const handle = u.username
        ? `@${u.username}`
        : u.first_name || "Signed in";
      refs.meBadge.textContent =
        handle + (state.me.is_primary ? " · primary admin" : "");
    }
    if (refs.accessSection) refs.accessSection.hidden = !state.me.is_primary;
    if (refs.chatsSection) refs.chatsSection.hidden = !state.me.is_primary;

    await Promise.all([refreshReferrers(), refreshReminders(), refreshSchedule()]);
    if (state.me.is_primary) await refreshAccess();
  } catch (err) {
    fail(err);
  }
}

async function refreshReferrers() {
  const data = await api("/referrers");
  state.referrers = data.referrers || [];
  state.leaderboard = data.leaderboard || [];
  state.summary = data.summary || null;
  renderComposeReferrers();
  renderPeople();
  configureMainButton();
}

async function refreshReminders() {
  const data = await api("/reminders");
  state.reminders = data.reminders || [];
  renderFeed();
}

async function refreshSchedule() {
  const data = await api("/announcements/schedule");
  state.schedule = data.schedule || { days: [], time_of_day: "" };
  renderFeed();
}

async function refreshAccess() {
  const [admins, chats] = await Promise.all([
    api("/admins"),
    api("/approved_chats"),
  ]);
  state.baseAdmins = admins.base || [];
  state.extraAdmins = admins.extras || [];
  state.approvedChats = { base: chats.base || [], entries: chats.entries || [] };
  renderAdmins();
  renderChats();
}

/* =========================================================
 * Compose: state machine
 * ========================================================= */

function setComposeMode(mode) {
  compose.mode = mode;
  refs.composeEl.dataset.mode = mode;
  refs.composeBodies.forEach((b) => {
    b.hidden = b.dataset.mode !== mode;
  });
  // "Schedule once" only makes sense for custom messages — spotlight
  // text is generated dynamically at fire time, so a static one-off
  // would just go stale.
  refs.composeWhenOnceLabel.hidden = mode !== "custom";
  if (mode === "spotlight" && compose.when === "once") {
    setComposeWhen("now");
  }
  refs.composeLede.textContent =
    mode === "spotlight"
      ? "Celebrate a driver. Now or on a recurring schedule."
      : "Write a one-off note, schedule it, or repeat it weekly.";
  for (const r of refs.composeModeRadios) r.checked = r.value === mode;
  renderComposePreview();
  configureMainButton();
}

function setComposeWhen(when) {
  compose.when = when;
  refs.composeWhenDetails.forEach((d) => {
    d.hidden = d.dataset.when !== when;
  });
  for (const r of refs.composeWhenRadios) r.checked = r.value === when;
  configureMainButton();
}

refs.composeModeRadios.forEach((r) =>
  r.addEventListener("change", () => setComposeMode(r.value)),
);
refs.composeWhenRadios.forEach((r) =>
  r.addEventListener("change", () => setComposeWhen(r.value)),
);

function renderComposeReferrers() {
  refs.composeReferrer.replaceChildren();
  if (!state.referrers.length) {
    refs.composeSpotEmpty.hidden = false;
    refs.composeReferrer.disabled = true;
    return;
  }
  refs.composeSpotEmpty.hidden = true;
  refs.composeReferrer.disabled = false;
  for (const ref of state.referrers) {
    const opt = document.createElement("option");
    opt.value = String(ref.id);
    const count = ref.referral_count;
    opt.textContent = `${ref.name} — ${count} referral${count === 1 ? "" : "s"}`;
    refs.composeReferrer.appendChild(opt);
  }
  renderComposePreview();
}

refs.composeReferrer.addEventListener("change", renderComposePreview);

function renderComposePreview() {
  if (!refs.composePreview) return;
  if (compose.mode !== "spotlight") {
    refs.composePreview.textContent = "";
    return;
  }
  const id = refs.composeReferrer.value;
  if (!id) {
    refs.composePreview.textContent = "";
    return;
  }
  const ref = state.referrers.find((r) => String(r.id) === id);
  if (!ref) return;
  const drivers = (ref.referrals || [])
    .filter((d) => !d.is_removed)
    .slice(0, 10);
  const cashPer = state.summary?.program?.cash_per_referral || 0;
  const lines = [
    `🎉 Congratulations to ${ref.name} for bringing ${ref.referral_count} referral${ref.referral_count === 1 ? "" : "s"}!`,
    "",
    "👥 Referred Friends:",
    ...(drivers.length
      ? drivers.map((d) => `• ${d.name}`)
      : ["• No referrals yet."]),
    "",
    `💵 Referral Bonus: $${(ref.referral_count * cashPer).toLocaleString()} total`,
    `📈 CPM Bonus: +${ref.bonus_cpm} CPM (${ref.base_cpm} → ${ref.new_cpm})`,
  ];
  refs.composePreview.textContent = lines.join("\n");
}

/* ---- Custom composer chips ---- */

refs.composeBold?.addEventListener("click", () =>
  wrapTextarea(refs.composeText, "b"),
);
refs.composeItalic?.addEventListener("click", () =>
  wrapTextarea(refs.composeText, "i"),
);

function wrapTextarea(textarea, tag) {
  const start = textarea.selectionStart ?? textarea.value.length;
  const end = textarea.selectionEnd ?? textarea.value.length;
  const value = textarea.value;
  const selected = value.slice(start, end) || "text";
  const wrapped = `<${tag}>${selected}</${tag}>`;
  textarea.value = value.slice(0, start) + wrapped + value.slice(end);
  textarea.focus();
  const cursor = start + wrapped.length;
  textarea.setSelectionRange(cursor, cursor);
}

refs.composePhotoBtn?.addEventListener("click", () =>
  refs.composePhoto.click(),
);
refs.composePhoto?.addEventListener("change", async () => {
  const file = refs.composePhoto.files?.[0];
  if (!file) return;
  if (DEMO_MODE) {
    state.composeMediaPath = "demo.jpg";
    refs.composePhotoStatus.textContent = `Photo attached — ${file.name}`;
    refs.composePhotoRemove.hidden = false;
    return;
  }
  try {
    const form = new FormData();
    form.append("file", file);
    const res = await fetch("/api/uploads/reminder_media", {
      method: "POST",
      body: form,
      headers: { "X-Telegram-Init-Data": initData },
    });
    if (!res.ok) {
      const payload = await res.json().catch(() => ({}));
      throw new Error(payload.error || "Upload failed");
    }
    const payload = await res.json();
    state.composeMediaPath = payload.path;
    refs.composePhotoStatus.textContent = `Photo attached — ${file.name}`;
    refs.composePhotoRemove.hidden = false;
  } catch (err) {
    refs.composePhoto.value = "";
    fail(err);
  }
});
refs.composePhotoRemove?.addEventListener("click", () => {
  state.composeMediaPath = null;
  refs.composePhoto.value = "";
  refs.composePhotoStatus.textContent = "No photo selected.";
  refs.composePhotoRemove.hidden = true;
});

/* ---- Compose: prefill helpers ---- */

function prefillForReferrer(ref) {
  setComposeMode("spotlight");
  refs.composeReferrer.value = String(ref.id);
  setComposeWhen("now");
  renderComposePreview();
  if (refs.composePreviewWrap && !refs.composePreviewWrap.open) {
    refs.composePreviewWrap.open = true;
  }
  scrollToCompose();
  flashCompose();
}

function prefillForSchedule() {
  setComposeMode("spotlight");
  if (state.referrers.length) {
    refs.composeReferrer.value = String(state.referrers[0].id);
  }
  setComposeWhen("repeat");
  // Pre-check the current schedule's days + time
  const days = new Set(state.schedule.days || []);
  refs.composeDays
    .querySelectorAll("input[type='checkbox']")
    .forEach((cb) => {
      cb.checked = days.has(Number(cb.value));
    });
  refs.composeRepeatTime.value = state.schedule.time_of_day || "10:00";
  scrollToCompose();
  flashCompose();
}

function scrollToCompose() {
  refs.composeEl.scrollIntoView({ behavior: "smooth", block: "start" });
}

function flashCompose() {
  if (window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches) return;
  refs.composeEl.animate(
    [
      { boxShadow: "0 0 0 0 transparent" },
      { boxShadow: "0 0 0 4px var(--accent-soft, rgba(255, 200, 100, 0.4))" },
      { boxShadow: "0 0 0 0 transparent" },
    ],
    { duration: 700, easing: "ease-out" },
  );
}

/* ---- Send ---- */

function gatherCustomPayload() {
  const text = refs.composeText.value.trim();
  if (!text) return null;
  const base = {
    text,
    media_path: state.composeMediaPath,
    ignore_inactive: refs.composeSkipInactive.checked,
  };
  return base;
}

function gatherRepeatDays() {
  return Array.from(
    refs.composeDays.querySelectorAll("input[type='checkbox']:checked"),
  ).map((cb) => Number(cb.value));
}

async function sendCompose() {
  try {
    if (compose.mode === "spotlight") {
      const refId = refs.composeReferrer.value;
      if (!refId) return tg.showAlert("Pick a driver to celebrate.");
      if (compose.when === "now") {
        const refRow = state.referrers.find((r) => String(r.id) === refId);
        const ok = await confirmAction(
          `Broadcast a spotlight for ${refRow?.name || "this referrer"} now?`,
        );
        if (!ok) return;
        tg.MainButton?.showProgress?.();
        await api(`/referrers/${refId}/announce`, {
          method: "POST",
          body: JSON.stringify({
            ignore_inactive: refs.composeSkipInactive.checked,
          }),
        });
        notify("Sent", "The spotlight is on its way.");
        return;
      }
      if (compose.when === "repeat") {
        const days = gatherRepeatDays();
        const time = refs.composeRepeatTime.value;
        if (!days.length) return tg.showAlert("Pick at least one day.");
        if (!time) return tg.showAlert("Pick a time.");
        const ok = await confirmAction(
          `Auto-broadcast a spotlight on the picked days at ${time}?`,
        );
        if (!ok) return;
        tg.MainButton?.showProgress?.();
        await api("/announcements/schedule", {
          method: "POST",
          body: JSON.stringify({ days, time_of_day: time }),
        });
        await refreshSchedule();
        notify("Saved", "Auto spotlight scheduled.");
        return;
      }
    }

    // custom
    const payload = gatherCustomPayload();
    if (!payload) return tg.showAlert("Add a message first.");
    if (compose.when === "now") {
      const ok = await confirmAction("Broadcast this message now?");
      if (!ok) return;
      tg.MainButton?.showProgress?.();
      await api("/reminders", {
        method: "POST",
        body: JSON.stringify({
          ...payload,
          mode: "once",
          send_now: true,
        }),
      });
      notify("Sent", "Message broadcast.");
      return;
    }
    if (compose.when === "once") {
      if (!refs.composeOnce.value) return tg.showAlert("Pick a date and time.");
      tg.MainButton?.showProgress?.();
      await api("/reminders", {
        method: "POST",
        body: JSON.stringify({
          ...payload,
          mode: "once",
          run_at: refs.composeOnce.value,
        }),
      });
      await refreshReminders();
      notify("Saved", "Scheduled to send.");
      return;
    }
    if (compose.when === "repeat") {
      const days = gatherRepeatDays();
      const time = refs.composeRepeatTime.value;
      if (!days.length) return tg.showAlert("Pick at least one day.");
      if (!time) return tg.showAlert("Pick a time.");
      tg.MainButton?.showProgress?.();
      await api("/reminders", {
        method: "POST",
        body: JSON.stringify({
          ...payload,
          mode: "schedule",
          days,
          time_of_day: time,
        }),
      });
      await refreshReminders();
      notify("Saved", "Repeating reminder created.");
      return;
    }
  } catch (err) {
    fail(err);
  } finally {
    tg.MainButton?.hideProgress?.();
  }
}

refs.composeSend?.addEventListener("click", sendCompose);

refs.composePreviewSelf?.addEventListener("click", async () => {
  try {
    if (compose.mode === "spotlight") {
      // Spotlight preview: show the rendered preview here in-page rather
      // than poke the user's chat with a separate message.
      if (refs.composePreviewWrap) refs.composePreviewWrap.open = true;
      refs.composePreview?.scrollIntoView({ behavior: "smooth", block: "nearest" });
      return;
    }
    const payload = gatherCustomPayload();
    if (!payload) return tg.showAlert("Add a message first.");
    const previewPayload = { ...payload, mode: "once" };
    if (compose.when === "repeat") {
      const days = gatherRepeatDays();
      previewPayload.mode = "schedule";
      previewPayload.days = days;
      previewPayload.time_of_day = refs.composeRepeatTime.value;
    }
    await api("/reminders/preview", {
      method: "POST",
      body: JSON.stringify(previewPayload),
    });
    notify("Sent to you", "Preview delivered to this chat.");
  } catch (err) {
    fail(err);
  }
});

/* =========================================================
 * MainButton wiring
 * ========================================================= */

function configureMainButton() {
  if (!tg.MainButton?.setParams) return;
  let label = "Send broadcast";
  if (compose.mode === "spotlight" && compose.when === "repeat") {
    label = "Save schedule";
  } else if (compose.mode === "custom" && compose.when === "once") {
    label = "Schedule message";
  } else if (compose.mode === "custom" && compose.when === "repeat") {
    label = "Save reminder";
  } else if (compose.mode === "spotlight") {
    label = "Broadcast spotlight";
  }
  const accent =
    getComputedStyle(document.documentElement)
      .getPropertyValue("--accent")
      .trim() || "#d18a3a";
  tg.MainButton.setParams({
    text: label,
    is_visible: true,
    is_active: true,
    color: accent,
    text_color: tg.colorScheme === "dark" ? "#1f1c17" : "#fffaf0",
  });
  tg.MainButton.offClick?.(sendCompose);
  tg.MainButton.onClick?.(sendCompose);
  tg.MainButton.show();
}

/* =========================================================
 * Render: Feed
 * ========================================================= */

function renderFeed() {
  refs.feedList.replaceChildren();

  const items = [];
  if (state.schedule?.days?.length) {
    items.push({
      kind: "Auto spotlight",
      title: formatScheduleSummary(state.schedule),
      when: `Repeats at ${state.schedule.time_of_day}`,
      tags: [],
      mark: "schedule",
      state: "active",
      actions: [
        {
          label: "Edit",
          variant: "ghost",
          handler: () => prefillForSchedule(),
        },
        {
          label: "Turn off",
          variant: "ghost-danger",
          handler: clearSchedule,
        },
      ],
    });
  }

  for (const rem of state.reminders) {
    const tags = [];
    if (rem.has_media) tags.push({ text: "Photo" });
    if (rem.ignore_inactive === false)
      tags.push({ text: "Reaches inactive groups", warn: true });
    if (!rem.active) tags.push({ text: "Paused" });
    items.push({
      kind: rem.type === "once" ? "Scheduled" : "Reminder",
      title: rem.text,
      when: rem.schedule,
      tags,
      mark: "reminder",
      state: rem.active ? "active" : "paused",
      actions: [
        {
          label: rem.active ? "Pause" : "Resume",
          variant: "ghost",
          handler: () => toggleReminder(rem),
        },
        {
          label: "Delete",
          variant: "ghost-danger",
          handler: () => deleteReminder(rem),
        },
      ],
    });
  }

  if (!items.length) {
    const note = document.createElement("p");
    note.className = "empty-note";
    note.textContent = "Nothing scheduled. The composer above is ready when you are.";
    refs.feedList.appendChild(note);
    return;
  }

  for (const item of items) {
    refs.feedList.appendChild(buildFeedItem(item));
  }
}

function buildFeedItem(item) {
  const node = refs.feedItemTpl.content.cloneNode(true);
  const article = node.querySelector(".feed-item");
  article.dataset.kind = item.mark;
  article.dataset.state = item.state;

  node.querySelector(".feed-item-kind").textContent = item.kind;
  node.querySelector(".feed-item-title").textContent = item.title;
  node.querySelector(".feed-item-when").textContent = item.when;

  const tagBox = node.querySelector(".feed-item-tags");
  if (!item.tags.length) tagBox.remove();
  else {
    for (const t of item.tags) {
      const chip = document.createElement("span");
      chip.className = "feed-tag" + (t.warn ? " feed-tag-warn" : "");
      chip.textContent = t.text;
      tagBox.appendChild(chip);
    }
  }

  const actionBox = node.querySelector(".feed-item-actions");
  for (const action of item.actions) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className =
      "btn btn-xs " +
      (action.variant === "ghost-danger"
        ? "btn-ghost btn-danger-ghost"
        : "btn-ghost");
    btn.textContent = action.label;
    btn.addEventListener("click", action.handler);
    actionBox.appendChild(btn);
  }

  return node;
}

async function toggleReminder(rem) {
  try {
    await api(`/reminders/${rem.id}`, {
      method: "PATCH",
      body: JSON.stringify({ active: !rem.active }),
    });
    await refreshReminders();
  } catch (err) {
    fail(err);
  }
}

async function deleteReminder(rem) {
  const ok = await confirmAction(`Delete this reminder?`);
  if (!ok) return;
  try {
    await api(`/reminders/${rem.id}`, { method: "DELETE" });
    await refreshReminders();
  } catch (err) {
    fail(err);
  }
}

async function clearSchedule() {
  const ok = await confirmAction(
    "Turn off the automatic spotlight? You can re-enable it any time.",
  );
  if (!ok) return;
  try {
    await api("/announcements/schedule", { method: "DELETE" });
    await refreshSchedule();
    notify("Off", "No automatic spotlight is scheduled.");
  } catch (err) {
    fail(err);
  }
}

const WEEKDAY_LONG = [
  "Mondays",
  "Tuesdays",
  "Wednesdays",
  "Thursdays",
  "Fridays",
  "Saturdays",
  "Sundays",
];

function formatScheduleSummary(schedule) {
  if (!schedule || !schedule.days?.length) return "No automatic spotlight";
  const days = schedule.days
    .filter((d) => d >= 0 && d <= 6)
    .map((d) => WEEKDAY_LONG[d]);
  if (!days.length) return "No automatic spotlight";
  if (days.length === 1) return `Every ${days[0]}`;
  if (days.length === 2) return `Every ${days[0]} and ${days[1]}`;
  return (
    "Every " +
    days.slice(0, -1).join(", ") +
    ", and " +
    days[days.length - 1]
  );
}

/* =========================================================
 * Render: People
 * ========================================================= */

function renderPeople() {
  refs.peopleList.replaceChildren();
  const q = state.searchQuery.trim().toLowerCase();
  const matches = state.referrers.filter((ref) => {
    if (!q) return true;
    if (ref.name.toLowerCase().includes(q)) return true;
    return (ref.referrals || []).some((d) =>
      d.name.toLowerCase().includes(q),
    );
  });

  if (!state.referrers.length) {
    const note = document.createElement("p");
    note.className = "empty-note";
    note.textContent =
      "Add your first referrer to start tracking who's bringing drivers in.";
    refs.peopleList.appendChild(note);
    return;
  }

  if (!matches.length) {
    const note = document.createElement("p");
    note.className = "empty-note";
    note.textContent = "No people or drivers match that search.";
    refs.peopleList.appendChild(note);
    return;
  }

  matches.forEach((ref, idx) => {
    refs.peopleList.appendChild(
      buildPersonRow(ref, idx === 0 && !q && ref.referral_count > 0),
    );
  });
}

function buildPersonRow(ref, isLeader) {
  const node = refs.personTpl.content.cloneNode(true);
  const person = node.querySelector(".person");
  person.dataset.referrerId = String(ref.id);
  if (isLeader) person.dataset.leader = "true";
  person.dataset.collapsed =
    state.expandedReferrer === ref.id ? "false" : "true";

  const fmt = (n) => Number(n).toFixed(2).replace(/\.00$/, "");
  const activeCount = (ref.referrals || []).filter((r) => !r.is_removed).length;
  const removedCount = (ref.referrals || []).filter((r) => r.is_removed).length;

  const titleEl = node.querySelector(".person-title");
  titleEl.textContent = ref.name;

  const statsEl = node.querySelector(".person-stats");
  // e.g. "5 referrals · 67 CPM · +4 bonus"
  const statsParts = [];
  statsParts.push(`${activeCount} ref${activeCount === 1 ? "." : "s."}`);
  const cpmFrag = document.createElement("span");
  const cpmStrong = document.createElement("strong");
  cpmStrong.textContent = `${fmt(ref.new_cpm)} CPM`;
  cpmFrag.appendChild(cpmStrong);
  statsEl.textContent = `${statsParts.join(" · ")} · `;
  statsEl.appendChild(cpmStrong);
  if (ref.bonus_cpm > 0) {
    const bonus = document.createElement("span");
    bonus.textContent = ` · +${fmt(ref.bonus_cpm)} bonus`;
    statsEl.appendChild(bonus);
  }

  // Drivers preview (first 3 names + "+N more")
  const driversLine = node.querySelector(".person-drivers-line");
  const visibleDrivers = (ref.referrals || [])
    .filter((d) => !d.is_removed)
    .slice(0, 3);
  if (!visibleDrivers.length) {
    const empty = document.createElement("span");
    empty.className = "empty";
    empty.textContent = "No drivers yet — tap to add.";
    driversLine.appendChild(empty);
  } else {
    visibleDrivers.forEach((d, i) => {
      if (i > 0) driversLine.appendChild(document.createTextNode(" · "));
      driversLine.appendChild(document.createTextNode(d.name));
    });
    const remaining = activeCount - visibleDrivers.length;
    if (remaining > 0) {
      const more = document.createElement("span");
      more.className = "more";
      more.textContent = ` · +${remaining} more`;
      driversLine.appendChild(more);
    }
  }

  // Expand button
  const expandBtn = node.querySelector(".person-expand");
  expandBtn.setAttribute(
    "aria-expanded",
    person.dataset.collapsed === "false" ? "true" : "false",
  );
  expandBtn.addEventListener("click", () => {
    state.expandedReferrer =
      state.expandedReferrer === ref.id ? null : ref.id;
    renderPeople();
  });

  // Spotlight button
  const spotBtn = node.querySelector(".person-spot");
  spotBtn.title = `Use ${ref.name} in composer`;
  spotBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    prefillForReferrer(ref);
  });

  // Detail (full driver list, CPM editor, actions)
  const detail = node.querySelector(".person-detail");
  detail.hidden = person.dataset.collapsed === "true";

  const driverList = node.querySelector(".person-driver-list");
  const activeDrivers = (ref.referrals || []).filter((d) => !d.is_removed);
  if (!activeDrivers.length) {
    const li = document.createElement("li");
    li.style.fontStyle = "italic";
    li.style.color = "var(--text-muted)";
    li.textContent = "No active drivers.";
    driverList.appendChild(li);
  } else {
    for (const driver of activeDrivers) {
      const li = document.createElement("li");
      const left = document.createElement("span");

      const name = document.createElement("span");
      name.className = "driver-name";
      name.textContent = driver.name;
      left.appendChild(name);

      const created = (driver.created_at || "").slice(0, 10);
      if (created) {
        const meta = document.createElement("span");
        meta.className = "driver-meta";
        meta.textContent = ` · ${created}`;
        left.appendChild(meta);
      }
      li.appendChild(left);

      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "driver-remove";
      remove.textContent = "Remove";
      remove.addEventListener("click", () => removeDriver(ref, driver));
      li.appendChild(remove);
      driverList.appendChild(li);
    }
  }

  // Removed drivers
  const removed = (ref.referrals || []).filter((d) => d.is_removed);
  const removedBox = node.querySelector(".person-removed");
  if (removed.length) {
    removedBox.hidden = false;
    removedBox.querySelector("summary").textContent =
      `${removed.length} removed`;
    const removedList = removedBox.querySelector(".person-removed-list");
    for (const d of removed) {
      const li = document.createElement("li");
      const removedDate = (d.removed_at || "").slice(0, 10);
      li.textContent = removedDate
        ? `${d.name} · removed ${removedDate}`
        : d.name;
      removedList.appendChild(li);
    }
  }

  // CPM editor
  const cpmInput = detail.querySelector('[data-action="cpm-input"]');
  cpmInput.value = fmt(ref.base_cpm);
  detail
    .querySelector('[data-action="cpm-save"]')
    .addEventListener("click", async () => {
      const value = Number(cpmInput.value);
      if (Number.isNaN(value)) return tg.showAlert("Enter a valid number.");
      try {
        await api(`/referrers/${ref.id}`, {
          method: "PATCH",
          body: JSON.stringify({ base_cpm: value }),
        });
        await refreshReferrers();
        notify("Saved", "Base CPM updated.");
      } catch (err) {
        fail(err);
      }
    });

  // Add drivers
  const addForm = detail.querySelector('[data-form="add-drivers"]');
  detail
    .querySelector('[data-action="add-drivers"]')
    .addEventListener("click", () => {
      addForm.hidden = !addForm.hidden;
      if (!addForm.hidden) addForm.querySelector("textarea").focus();
    });
  detail
    .querySelector('[data-action="cancel-add"]')
    .addEventListener("click", () => {
      addForm.hidden = true;
    });
  addForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const ta = addForm.querySelector("textarea");
    const names = ta.value.trim();
    if (!names) return;
    try {
      await api(`/referrers/${ref.id}/referrals`, {
        method: "POST",
        body: JSON.stringify({ names }),
      });
      ta.value = "";
      addForm.hidden = true;
      await refreshReferrers();
      notify("Saved", "Drivers added.");
    } catch (err) {
      fail(err);
    }
  });

  // Remove referrer
  detail
    .querySelector('[data-action="remove-referrer"]')
    .addEventListener("click", async () => {
      const ok = await confirmAction(
        `Remove ${ref.name} and all their referrals? This can't be undone.`,
      );
      if (!ok) return;
      try {
        // Backend has no DELETE for referrers in the WebApp API; use the
        // mark-removed pattern by removing all referrals + the referrer
        // is not directly supported. Surface a useful error.
        // Reuse the bot-side flow: call the referrals delete endpoint per id,
        // then there's nothing to actually delete the referrer entry from
        // the WebApp. Tell the user to use the bot for this rare action.
        tg.showAlert(
          "Removing a referrer entirely is only available via the bot's /referrals → Remove referrer flow.",
        );
      } catch (err) {
        fail(err);
      }
    });

  return node;
}

async function removeDriver(ref, driver) {
  const ok = await confirmAction(`Remove ${driver.name} from ${ref.name}?`);
  if (!ok) return;
  try {
    await api(`/referrers/${ref.id}/referrals/remove`, {
      method: "POST",
      body: JSON.stringify({ referral_ids: [driver.id] }),
    });
    await refreshReferrers();
  } catch (err) {
    fail(err);
  }
}

refs.peopleSearch?.addEventListener("input", (e) => {
  state.searchQuery = e.target.value;
  renderPeople();
});

refs.addReferrerToggle?.addEventListener("click", () => {
  const willShow = refs.addReferrerForm.hidden;
  refs.addReferrerForm.hidden = !willShow;
  if (willShow) refs.refName.focus();
});
refs.addReferrerCancel?.addEventListener("click", () => {
  refs.addReferrerForm.hidden = true;
});
refs.addReferrerForm?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const name = refs.refName.value.trim();
  const baseCpm = Number(refs.refCpm.value);
  if (!name || Number.isNaN(baseCpm))
    return tg.showAlert("Add a name and a valid base CPM.");
  try {
    await api("/referrers", {
      method: "POST",
      body: JSON.stringify({ name, base_cpm: baseCpm }),
    });
    refs.refName.value = "";
    refs.refCpm.value = "";
    refs.addReferrerForm.hidden = true;
    await refreshReferrers();
    notify("Saved", `${name} added.`);
  } catch (err) {
    fail(err);
  }
});

/* =========================================================
 * Drawer (settings)
 * ========================================================= */

function openDrawer() {
  refs.drawer.hidden = false;
  refs.drawer.querySelector(".drawer-panel").focus?.();
}

function closeDrawer() {
  refs.drawer.hidden = true;
}

refs.settingsBtn?.addEventListener("click", openDrawer);
document.querySelectorAll("[data-drawer-close]").forEach((b) =>
  b.addEventListener("click", closeDrawer),
);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !refs.drawer.hidden) closeDrawer();
});

/* ---- Export ---- */

refs.exportBtn?.addEventListener("click", async () => {
  try {
    const blob = await apiBlob("/referrals/export");
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "referrals.xlsx";
    a.click();
    URL.revokeObjectURL(url);
  } catch (err) {
    fail(err);
  }
});

/* ---- Admins / Chats ---- */

function renderAdmins() {
  if (!refs.baseAdminList) return;
  refs.baseAdminList.replaceChildren();
  for (const admin of state.baseAdmins) {
    const li = document.createElement("li");
    const primary = document.createElement("span");
    primary.className = "entity-primary";
    const name = document.createElement("span");
    name.className = "entity-name";
    name.textContent = admin.username ? `@${admin.username}` : `ID ${admin.id}`;
    primary.appendChild(name);
    const tag = document.createElement("span");
    tag.className = "entity-tag";
    tag.textContent = admin.is_primary ? "Primary" : "Base";
    primary.appendChild(tag);
    li.appendChild(primary);
    refs.baseAdminList.appendChild(li);
  }

  refs.extraAdminList.replaceChildren();
  for (const admin of state.extraAdmins) {
    const li = document.createElement("li");
    const primary = document.createElement("span");
    primary.className = "entity-primary";
    const name = document.createElement("span");
    name.className = "entity-name";
    name.textContent = admin.username
      ? `@${admin.username}`
      : `ID ${admin.user_id}`;
    primary.appendChild(name);
    li.appendChild(primary);
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "Remove";
    remove.addEventListener("click", () => removeAdmin(admin));
    li.appendChild(remove);
    refs.extraAdminList.appendChild(li);
  }
}

function renderChats() {
  if (!refs.chatList) return;
  refs.chatList.replaceChildren();

  for (const chat of state.approvedChats.base || []) {
    const li = document.createElement("li");
    const primary = document.createElement("span");
    primary.className = "entity-primary";
    const name = document.createElement("span");
    name.className = "entity-name";
    name.textContent = chat.title || `Chat ${chat.chat_id}`;
    primary.appendChild(name);
    const tag = document.createElement("span");
    tag.className = "entity-tag";
    tag.textContent = "Configured";
    primary.appendChild(tag);
    li.appendChild(primary);
    refs.chatList.appendChild(li);
  }

  for (const chat of state.approvedChats.entries || []) {
    const li = document.createElement("li");
    const primary = document.createElement("span");
    primary.className = "entity-primary";
    const name = document.createElement("span");
    name.className = "entity-name";
    name.textContent = chat.title || `Chat ${chat.chat_id}`;
    primary.appendChild(name);
    li.appendChild(primary);

    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "Remove";
    remove.addEventListener("click", () => removeChat(chat));
    li.appendChild(remove);
    refs.chatList.appendChild(li);
  }
}

async function removeAdmin(admin) {
  const ok = await confirmAction("Remove this admin?");
  if (!ok) return;
  try {
    await api(`/admins/${admin.user_id}`, { method: "DELETE" });
    await refreshAccess();
  } catch (err) {
    fail(err);
  }
}

async function removeChat(chat) {
  const ok = await confirmAction("Remove this chat?");
  if (!ok) return;
  try {
    await api(`/approved_chats/${chat.chat_id}`, { method: "DELETE" });
    await refreshAccess();
  } catch (err) {
    fail(err);
  }
}

refs.adminForm?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const username = refs.adminUsername.value.trim();
  if (!username) return;
  try {
    await api("/admins", {
      method: "POST",
      body: JSON.stringify({ username }),
    });
    refs.adminUsername.value = "";
    await refreshAccess();
    notify("Saved", "Admin added.");
  } catch (err) {
    fail(err);
  }
});

refs.chatForm?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const chatId = refs.chatId.value.trim();
  if (!chatId) return;
  try {
    await api("/approved_chats", {
      method: "POST",
      body: JSON.stringify({
        chat_id: chatId,
        title: refs.chatTitle.value,
      }),
    });
    refs.chatId.value = "";
    refs.chatTitle.value = "";
    await refreshAccess();
    notify("Saved", "Chat added.");
  } catch (err) {
    fail(err);
  }
});

/* =========================================================
 * Demo data
 * ========================================================= */

function demoApi(path, options = {}) {
  const method = (options.method || "GET").toUpperCase();
  return new Promise((resolve) =>
    setTimeout(() => resolve(demoResponse(path, method)), 100),
  );
}

function demoResponse(path, method) {
  if (path === "/me") {
    return {
      ok: true,
      user: { id: 1, username: "anya", first_name: "Anya" },
      is_primary: true,
    };
  }
  if (path === "/referrers") {
    return {
      ok: true,
      summary: {
        total_referrals: 14,
        total_cash: 7000,
        program: { step: 2, step_bonus: 2, cash_per_referral: 500 },
      },
      referrers: [
        {
          id: 1,
          name: "Jean Cadet",
          base_cpm: 63,
          referral_count: 5,
          bonus_cpm: 4,
          new_cpm: 67,
          referrals: [
            { id: 11, name: "Noah Anita", created_at: "2026-04-21T09:00:00", is_removed: false },
            { id: 12, name: "Rahat Abdul Aziz", created_at: "2026-04-19T10:30:00", is_removed: false },
            { id: 13, name: "Jean Joseph", created_at: "2026-04-18T08:00:00", is_removed: false },
            { id: 14, name: "Leger Pierre", created_at: "2026-04-15T11:00:00", is_removed: false },
            { id: 15, name: "Jane Cooper", created_at: "2026-04-13T14:30:00", is_removed: false },
          ],
        },
        {
          id: 2,
          name: "Peter Summers",
          base_cpm: 60,
          referral_count: 4,
          bonus_cpm: 4,
          new_cpm: 64,
          referrals: [
            { id: 21, name: "Mia Cole", created_at: "2026-04-22T08:00:00", is_removed: false },
            { id: 22, name: "Dustin Kim", created_at: "2026-04-20T09:00:00", is_removed: false },
            { id: 23, name: "Ana Reyes", created_at: "2026-04-17T11:00:00", is_removed: false },
            { id: 24, name: "Tom Daly", created_at: "2026-04-12T10:00:00", is_removed: true, removed_at: "2026-04-22T15:00:00" },
          ],
        },
        {
          id: 3,
          name: "Claudio Marcel",
          base_cpm: 62,
          referral_count: 3,
          bonus_cpm: 2,
          new_cpm: 64,
          referrals: [
            { id: 31, name: "Felix Hou", created_at: "2026-04-22T09:00:00", is_removed: false },
            { id: 32, name: "Ren Park", created_at: "2026-04-18T11:00:00", is_removed: false },
            { id: 33, name: "Sasha Quinn", created_at: "2026-04-14T14:00:00", is_removed: false },
          ],
        },
      ],
      leaderboard: [
        { name: "Jean Cadet", count: 5, bonus: 4 },
        { name: "Peter Summers", count: 4, bonus: 4 },
        { name: "Claudio Marcel", count: 3, bonus: 2 },
      ],
    };
  }
  if (path === "/reminders") {
    return {
      ok: true,
      reminders: [
        {
          id: 1,
          text: "Big morning, team — drivers in by 7am please.",
          schedule: "[Daily] 06:30",
          active: true,
          ignore_inactive: true,
          has_media: false,
          type: "daily",
        },
        {
          id: 2,
          text: "Friday wrap — close out paperwork before the weekend.",
          schedule: "[Weekly Fri] 16:00",
          active: false,
          ignore_inactive: true,
          has_media: true,
          type: "weekly",
        },
      ],
    };
  }
  if (path === "/announcements/schedule") {
    return { ok: true, schedule: { days: [0, 3], time_of_day: "10:00" } };
  }
  if (path === "/admins") {
    return {
      ok: true,
      base: [{ id: 140802473, username: "anya", is_primary: true }],
      extras: [{ user_id: 555, username: "ops_marcus" }],
    };
  }
  if (path === "/approved_chats") {
    return {
      ok: true,
      base: [{ chat_id: -5035929357, title: "Operations" }],
      entries: [{ chat_id: -100123, title: "Weekend dispatchers" }],
    };
  }
  if (method === "DELETE" && path === "/announcements/schedule") {
    return { ok: true, schedule: { days: [], time_of_day: "" } };
  }
  return { ok: true };
}

/* =========================================================
 * Boot
 * ========================================================= */

setComposeMode("spotlight");
setComposeWhen("now");
bootstrap();
