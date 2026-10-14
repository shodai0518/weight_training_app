// アプリの入口：ログイン・タブの切りかえ・知らせ（ベル）

import { $, api, closeSheet, esc, formatTime, openSheet, openWho, run, state, toast } from "./api.js";

// ポジションを選ぶ（最初に 聞く。「自分」からも 開ける）。done(position)：選んだあと
export function askPosition({ first = false, done = () => {} } = {}) {
  const sheet = openSheet(first ? "ポジションを 選んでね" : "ポジションを変える", `
    <p class="hint">${first ? "トレーナーが ポジションごとに メニューを配れるように、最初に 選んでね（あとから「自分」で 変えられます）" : "今のポジション：" + esc(state.me.position || "未設定")}</p>
    <div class="position-picks">${state.positions.map((p) =>
      `<button type="button" class="chip${p === state.me.position ? " on" : ""}" data-pick-position="${esc(p)}">${esc(p)}</button>`).join("")}</div>`);
  sheet.querySelectorAll("[data-pick-position]").forEach((button) => button.addEventListener("click", () => run(button, async () => {
    const { position } = await api("/me/position", { method: "PUT", body: { position: button.dataset.pickPosition } });
    state.me.position = position;
    closeSheet();
    toast(`ポジションを「${position}」にしました`);
    done(position);
  })));
}
import { refreshInboxCount, renderAdmin } from "./admin.js";
import { renderMember, renderMembers } from "./member.js";
import { registerWorker } from "./notify.js";
import { restoreRest, stopRest } from "./rest.js";
import { renderPost, renderTimeline } from "./timeline.js";
import { refreshMenuBadge, renderWorkout } from "./workout.js";

const view = $("#view");
const TAB_OF = { timeline: "timeline", post: "timeline", workout: "workout", members: "members", member: "members", me: "me", admin: "admin" };
let visit = 0; // 画面を切りかえた回数（前の画面の読みこみが あとから終わっても、今の画面を上書きしないように）

async function go(screen, arg = null) {
  const mine = ++visit;
  const tab = screen === "member" && arg === state.me.id ? "me" : TAB_OF[screen];
  document.querySelectorAll("#tabs [data-tab]").forEach((button) => button.classList.toggle("on", button.dataset.tab === tab));
  window.scrollTo(0, 0);
  // 読みこみ中は 別の入れ物に描いて、終わってから 入れかえる
  const stage = document.createElement("div");
  try {
    if (screen === "timeline") await renderTimeline(stage, { userId: arg });
    else if (screen === "post") await renderPost(stage, arg);
    else if (screen === "workout") await renderWorkout(stage);
    else if (screen === "members") await renderMembers(stage);
    else if (screen === "member") await renderMember(stage, arg);
    else if (screen === "me") await renderMember(stage, state.me.id);
    else if (screen === "admin") await renderAdmin(stage);
  } catch (error) {
    stage.innerHTML = `<p class="hint center">${esc(error.message)}</p>`;
  }
  if (mine !== visit || !state.me) return;
  view.replaceChildren(stage);
  refreshCounts();
}

async function refreshCounts() {
  try {
    setBell((await api("/notices/count")).unread);
  } catch {
    // 数が出せなくても 続ける
  }
  refreshInboxCount();
  refreshMenuBadge();
}

function setBell(count) {
  const badge = $("#bell-count");
  badge.hidden = !count;
  badge.textContent = count > 99 ? "99+" : count;
}

// ---- ログインの前の画面 ----

async function renderAuth(mode = "login") {
  $("#top").hidden = true;
  $("#tabs").hidden = true;
  const setup = await api("/setup").catch(() => ({ needs_operator_code: false, sports: {} }));
  const fields = {
    login: `
      <label>ログインID<input name="login_id" autocomplete="username" autocapitalize="none" required></label>
      <label>パスワード<input name="password" type="password" autocomplete="current-password" required></label>`,
    join: `
      <label>招待の合言葉<input name="invite_code" autocapitalize="characters" autocomplete="off" required placeholder="トレーナーに聞いてね"></label>
      <label>名前（みんなに見える）<input name="name" maxlength="20" required></label>
      <label>ログインID（半角の英数字）<input name="login_id" autocomplete="username" autocapitalize="none" required></label>
      <label>パスワード（8文字以上）<input name="password" type="password" autocomplete="new-password" minlength="8" required></label>`,
    create: `
      <label>チームの名前<input name="team_name" maxlength="30" required></label>
      <label>競技<select name="sport" required><option value="">選んでね</option>${Object.entries(setup.sports ?? {}).map(([key, label]) =>
        `<option value="${key}">${esc(label)}</option>`).join("")}</select></label>
      <label>自分の名前（みんなに見える）<input name="name" maxlength="20" required></label>
      <label>ログインID（半角の英数字）<input name="login_id" autocomplete="username" autocapitalize="none" required></label>
      <label>パスワード（8文字以上）<input name="password" type="password" autocomplete="new-password" minlength="8" required></label>
      ${setup.needs_operator_code ? '<label>運営用の合言葉<input name="operator_code" type="password" autocomplete="off" required></label>' : ""}
      <p class="hint">作った人が そのチームの 最初のトレーナーになります</p>`,
  };
  const labels = { login: "ログイン", join: "チームに入る", create: "チームを作る" };
  const paths = { login: "/login", join: "/register", create: "/teams" };
  view.innerHTML = `
    <div class="auth">
      <img src="/icon.svg" alt="" class="logo">
      <div class="filter">${Object.entries(labels).map(([key, label]) =>
        `<button type="button" data-mode="${key}" class="${key === mode ? "on" : ""}">${label}</button>`).join("")}</div>
      <form id="auth-form" class="card form">${fields[mode]}
        <button type="submit" class="primary wide">${labels[mode]}</button></form>
    </div>`;
  view.querySelectorAll("[data-mode]").forEach((button) => button.addEventListener("click", () => renderAuth(button.dataset.mode)));
  $("#auth-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const body = Object.fromEntries(new FormData(event.target));
    run(event.target.querySelector("button[type=submit]"), async () => {
      await api(paths[mode], { method: "POST", body });
      await boot();
    });
  });
}

// ---- 知らせ（ベル）----

async function openNotices() {
  const notices = await api("/notices");
  const body = openSheet("知らせ", notices.length
    ? notices.map((n) => `<button type="button" class="list-row${n.unread ? " unread" : ""}" data-post="${n.post_id}" data-notice="${n.id}">
        <span>${esc(n.text)}</span><span class="hint">${formatTime(n.created_at)}</span></button>`).join("")
    : '<p class="hint center">まだ知らせは ありません</p>');
  body.querySelectorAll("[data-post]").forEach((row) => row.addEventListener("click", () => {
    closeSheet();
    const notice = notices.find((n) => n.id === Number(row.dataset.notice));
    const postId = Number(row.dataset.post);
    if (notice?.kind === "reaction" && notice.who?.length) {
      return openWho(notice.who, { after: { label: "投稿を見る", action: () => go("post", postId) } });
    }
    go("post", postId);
  }));
  if (notices.some((n) => n.unread)) {
    await api("/notices/read", { method: "POST" });
    setBell(0);
  }
}

$("#bell").addEventListener("click", (event) => run(event.currentTarget, openNotices));
$("#sheet-close").addEventListener("click", closeSheet);
$("#sheet").addEventListener("click", (event) => {
  if (event.target.id === "sheet") closeSheet();
});
$("#viewer").addEventListener("click", () => ($("#viewer").hidden = true));
$("#who-close").addEventListener("click", () => ($("#who").hidden = true));
$("#who").addEventListener("click", (event) => {
  if (event.target.id === "who") $("#who").hidden = true;
});
// data-zoom の付いた写真は、押すと 大きく出す（メニューの写真など）
document.addEventListener("click", (event) => {
  const image = event.target.closest("img[data-zoom]");
  if (!image) return;
  $("#viewer img").src = image.src;
  $("#viewer").hidden = false;
});
document.querySelectorAll("#tabs [data-tab]").forEach((button) => button.addEventListener("click", () => go(button.dataset.tab)));
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && state.me) refreshCounts();
});

// ---- 始まり ----

async function boot() {
  let data;
  try {
    data = await api("/me");
  } catch {
    return renderAuth();
  }
  Object.assign(state, {
    me: data.user, team: data.team, parts: data.parts, kinds: data.kinds, reactions: data.reactions, limits: data.limits, vapidKey: data.vapid_key,
    positions: data.positions ?? [],
  });
  $("#team-name").textContent = data.team.name;
  $("#top").hidden = false;
  $("#tabs").hidden = false;
  document.querySelectorAll("#tabs [data-trainer]").forEach((button) => (button.hidden = data.user.role !== "trainer"));
  setBell(data.unread);
  restoreRest();
  // 通知を押して開いたとき（/?post=番号）は、その投稿を出す
  if (new URLSearchParams(location.search).has("menu")) { // 「新しいメニュー」の通知を押して開いたとき
    history.replaceState(null, "", "/");
    return go("workout");
  }
  const postId = Number(new URLSearchParams(location.search).get("post"));
  if (postId) {
    history.replaceState(null, "", "/");
    return go("post", postId);
  }
  const { workout } = await api("/workouts/active").catch(() => ({ workout: null }));
  await go(workout ? "workout" : "timeline"); // 筋トレの途中なら 記録の画面から
  if (state.me.role === "member" && state.positions.length && !state.me.position) askPosition({ first: true });
}

state.go = go;
state.onLoggedOut = () => {
  if (!state.me) return;
  state.me = null;
  visit++;
  stopRest();
  toast("ログアウトしました");
  renderAuth();
};

registerWorker();
boot();
