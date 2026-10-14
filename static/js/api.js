// サーバーとのやりとりと、どの画面でも使う 小さな道具

export const state = {
  me: null, // ログインしている人
  team: null,
  parts: {},
  reactions: [],
  limits: {},
  vapidKey: null,
  go: () => {}, // 画面を切りかえる（app.js が入れる）
  onLoggedOut: () => {},
};

export async function api(path, { method = "GET", body } = {}) {
  let res;
  try {
    res = await fetch("/api" + path, {
      method,
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
      credentials: "same-origin",
    });
  } catch {
    throw new Error("つながりませんでした。少し待って もう一度ためしてね");
  }
  const data = await res.json().catch(() => null);
  if (res.status === 401 && state.me) state.onLoggedOut();
  if (!res.ok) throw new Error(typeof data?.detail === "string" ? data.detail : "うまくいきませんでした");
  return data;
}

export const $ = (selector, root = document) => root.querySelector(selector);
export const pad = (n) => String(n).padStart(2, "0");

const ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
export const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ESCAPES[c]);

let toastTimer = null;
export function toast(message) {
  const box = $("#toast");
  box.textContent = message;
  box.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (box.hidden = true), 3000);
}

// ボタンを押したときの処理：失敗したら 理由を出す。二度押しを防ぐ
export async function run(button, action) {  // button は なくてもいい（null）
  if (button?.disabled) return;
  if (button) button.disabled = true;
  try {
    return await action();
  } catch (error) {
    toast(error.message);
  } finally {
    if (button) button.disabled = false;
  }
}

const WEEKDAYS = ["日", "月", "火", "水", "木", "金", "土"];

export function formatDay(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  const date = new Date(y, m - 1, d);
  return `${m}/${d}（${WEEKDAYS[date.getDay()]}）`;
}

export function formatTime(iso) {
  const date = new Date(iso);
  const days = Math.floor((new Date().setHours(0, 0, 0, 0) - new Date(date).setHours(0, 0, 0, 0)) / 86400000);
  const clock = `${date.getHours()}:${pad(date.getMinutes())}`;
  if (days === 0) return `今日 ${clock}`;
  if (days === 1) return `きのう ${clock}`;
  return `${date.getMonth() + 1}/${date.getDate()} ${clock}`;
}

// 線のアイコン（絵文字のかわり。色は まわりの文字と同じになる）
const ICON_PATHS = {
  menu: '<rect x="5" y="4" width="14" height="17" rx="2"/><path d="M9 4.5V3h6v1.5M9 10h6M9 14h6"/>',
  camera: '<path d="M4 8h3l1.5-2.5h7L17 8h3v11H4z"/><circle cx="12" cy="13.2" r="3.4"/>',
  calendar: '<rect x="4" y="5" width="16" height="15" rx="2"/><path d="M4 10h16M8 3v4M16 3v4"/>',
  comment: '<path d="M4 5h16v11H9.5L5 20v-4H4z"/>',
  note: '<path d="M5 4h10l4 4v12H5z"/><path d="M15 4v4h4M8.5 12.5h7M8.5 16h5"/>',
  edit: '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="M13.5 6.5l4 4"/>',
};
export const icon = (name) => `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">${ICON_PATHS[name]}</svg>`;

// 名前の横に出す 丸いアイコン（名前の 1文字目。トレーナーは赤）
export const avatar = (user) =>
  `<span class="avatar${user.role === "trainer" ? " trainer" : ""}" aria-hidden="true">${esc([...String(user.name ?? "？")][0] ?? "？")}</span>`;

// ポジションの印（例：OL）。決めていなければ 何も出さない
export const positionTag = (user) => (user?.position ? `<span class="pos">${esc(user.position)}</span>` : "");

// 人を ポジションごとに 分ける（チームのポジションの順。決めていない人は「未設定」、トレーナーは 最後に まとめる）
export function groupByPosition(users) {
  const groups = [];
  const add = (label, members) => members.length && groups.push({ label, members });
  for (const position of state.positions ?? []) add(position, users.filter((u) => u.role !== "trainer" && u.position === position));
  add("未設定", users.filter((u) => u.role !== "trainer" && !(state.positions ?? []).includes(u.position)));
  add("トレーナー", users.filter((u) => u.role === "trainer"));
  return groups;
}

// メニューの期間（「10/12（月）〜10/18（日）」）と、今の期間かどうかの印
export function menuPeriodHtml(menu) {
  const from = menu.start_day ? formatDay(menu.start_day) : "";
  const to = menu.end_day ? formatDay(menu.end_day) : "";
  const period = from || to ? `${from}〜${to}` : "";
  const mark = { now: "", soon: '<span class="tag">これから</span>', over: '<span class="tag off">期間おわり</span>' }[menu.status] ?? "";
  return period || mark ? `<p class="menu-period">${period ? `${icon("calendar")}${period}` : ""}${mark}</p>` : "";
}

// メニューの写真（押すと 大きく出る）
export function menuPhotosHtml(menu, compact = false) {
  if (!menu.photos?.length) return "";
  return `<div class="menu-photos${compact ? " compact" : ""}">${menu.photos.map((p) =>
    `<img src="${p.url}" alt="メニューの写真" loading="lazy" data-zoom>`).join("")}</div>`;
}

// メニューの中身（期間・コメント・写真・種目）
export function menuDetailHtml(menu) {
  const items = menu.items.length
    ? `<ul class="menu-items">${menu.items.map((item) => `<li><span>${esc(item.name)}</span><span>${esc(item.label)}</span></li>`).join("")}</ul>`
    : "";
  return `${menuPeriodHtml(menu)}${menu.note ? `<p class="menu-note">${esc(menu.note)}</p>` : ""}${menuPhotosHtml(menu)}${items}`;
}

// メニューの一覧を 下から出す。今のメニューを上に、期間がすぎたものは「過去のメニュー」として下に分ける
export function openMenus(menus, { onlyPast = false } = {}) {
  const fold = (menu, open) => `<details class="menu-fold"${open ? " open" : ""}>
    <summary><strong>${icon("menu")}${esc(menu.title)}</strong></summary>${menuDetailHtml(menu)}</details>`;
  const current = menus.filter((menu) => menu.status !== "over");
  const past = menus.filter((menu) => menu.status === "over");
  const html = onlyPast
    ? past.map((menu) => fold(menu, past.length === 1)).join("")
    : current.map((menu) => fold(menu, current.length === 1)).join("") +
      (past.length ? `<p class="fold-title">過去のメニュー</p>${past.map((menu) => fold(menu, false)).join("")}` : "");
  return openSheet(onlyPast ? "過去のメニュー" : "配られたメニュー", html || '<p class="hint center">メニューは ありません</p>');
}

// ---- 記録の仕方ごとの 入れる欄 ----
// [欄の名前, 見出し, 単位, 刻み, キーボード, うすく出す字]
export const KIND_FIELDS = {
  weight_reps: [["weight", "重さ", "kg", "0.5", "decimal", "自重"], ["reps", "回数", "回", "1", "numeric"]],
  reps: [["reps", "回数", "回", "1", "numeric"]],
  count: [["reps", "本数", "本", "1", "numeric"]],
  time: [["seconds", "時間", "秒", "5", "numeric"]],
  minutes: [["duration_min", "時間", "分", "1", "decimal"]],
  distance_sets: [["distance_m", "距離", "m", "5", "decimal"], ["reps", "本数", "本", "1", "numeric"]],
  cardio: [["duration_min", "時間", "分", "1", "decimal"], ["distance_km", "距離", "km", "0.1", "decimal"]],
};

// withIds：記録の画面の 一番上の欄だけ、id も付ける（#weight など）
export function kindFieldsHtml(kind, values = {}, { withIds = false } = {}) {
  return (KIND_FIELDS[kind] ?? KIND_FIELDS.weight_reps).map(([name, label, unit, step, mode, placeholder]) =>
    `<label>${label} <input data-field="${name}"${withIds ? ` id="${name}"` : ""} type="number" inputmode="${mode}" min="0" step="${step}"
      value="${values?.[name] ?? ""}"${placeholder ? ` placeholder="${placeholder}"` : ""}> ${unit}</label>`).join("");
}

export function readKindFields(root) {
  const values = {};
  root.querySelectorAll("[data-field]").forEach((input) => {
    values[input.dataset.field] = input.value === "" ? null : Number(input.value);
  });
  return values;
}

// ---- 写真を スマホの中で小さくする（長い辺 1600px の JPEG）。撮った場所などの情報も ここで消える ----
const PHOTO_MAX_SIDE = 1600;
const PHOTO_QUALITY = 0.8;

export async function shrinkPhoto(file) {
  let source;
  try {
    source = await createImageBitmap(file, { imageOrientation: "from-image" });
  } catch {
    source = await new Promise((resolve, reject) => {
      const image = new Image();
      image.onload = () => resolve(image);
      image.onerror = () => reject(new Error("写真が読めませんでした"));
      image.src = URL.createObjectURL(file);
    });
  }
  const scale = Math.min(1, PHOTO_MAX_SIDE / Math.max(source.width, source.height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(source.width * scale);
  canvas.height = Math.round(source.height * scale);
  canvas.getContext("2d").drawImage(source, 0, 0, canvas.width, canvas.height);
  return canvas.toDataURL("image/jpeg", PHOTO_QUALITY);
}

// ---- スタンプした人の一覧（Slack のように、絵文字ごとに分けて見られる）----
// people：[{ name, emoji }]。after：「投稿を見る」などのボタン（なくてもいい）
export function openWho(people, { after = null } = {}) {
  const layer = $("#who");
  const emojis = state.reactions.filter((e) => people.some((p) => p.emoji === e));
  const draw = (tab) => {
    const shown = tab === "all" ? people : people.filter((p) => p.emoji === tab);
    $("#who-body").innerHTML = `
      <div class="who-tabs">
        <button type="button" data-who="all" class="${tab === "all" ? "on" : ""}">すべて ${people.length}</button>
        ${emojis.map((e) => `<button type="button" data-who="${e}" class="${tab === e ? "on" : ""}">${e} ${people.filter((p) => p.emoji === e).length}</button>`).join("")}
      </div>
      <ul class="who-list">${shown.map((p) => `<li><span class="who-emoji">${p.emoji}</span>${esc(p.name)}</li>`).join("")
        || '<li class="hint">まだ ありません</li>'}</ul>
      ${after ? `<button type="button" class="dark wide" id="who-after">${esc(after.label)}</button>` : ""}`;
    $("#who-body").querySelectorAll("[data-who]").forEach((b) => b.addEventListener("click", () => draw(b.dataset.who)));
    $("#who-after")?.addEventListener("click", () => {
      layer.hidden = true;
      after.action();
    });
  };
  draw("all");
  layer.hidden = false;
}

// 投稿の スタンプ（[{emoji, names}]）を、openWho の形に
export const reactionPeople = (reactions) => reactions.flatMap((r) => r.names.map((name) => ({ name, emoji: r.emoji })));

// 下から出る 小さな画面
export function openSheet(title, html) {
  $("#sheet-title").textContent = title;
  $("#sheet-body").innerHTML = html;
  $("#sheet").hidden = false;
  return $("#sheet-body");
}

export function closeSheet() {
  $("#sheet").hidden = true;
  $("#sheet-body").innerHTML = "";
}
