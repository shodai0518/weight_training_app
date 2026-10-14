// 筋トレの記録（始める → セットを追加 → 終わる → 投稿する）
// 始め方は 3つ：ふつうに始める／トレーナーのメニューで始める／あとから記録する
// 入れる欄は 種目の「記録の仕方」で変わる（重さ×回数・回数だけ・時間・距離×本数・有酸素）

import {
  $, api, closeSheet, esc, formatDay, icon, kindFieldsHtml, menuDetailHtml, menuPeriodHtml, menuPhotosHtml, openMenus, openSheet,
  pad, readKindFields, run, shrinkPhoto, state, toast,
} from "./api.js";
import * as notify from "./notify.js";
import { startRest, stopRest } from "./rest.js";

const BACKFILL_DAYS = 7; // 何日前の分まで あとから記録できるか（サーバーと同じ数字）

let catalog = null; // 種目の一覧（前回の記録・休憩の長さ付き）
let menuList = []; // 配られているメニュー（筋トレ中の画面に出す）
let menuOpen = false; // 筋トレ中の画面で、メニューを開いているか（最初は閉じておく。開いたら そのままにしておく）
let selectedPart = "chest";
let selectedExercise = null;
let clockTimer = null;

// 新しいメニューが配られたら、「記録」のタブに 印を出す（この画面を開くと 消える）
const MENU_SEEN_KEY = "menu_seen";
const newestMenu = (menus) => Math.max(0, ...menus.map((menu) => menu.id));

function markMenusSeen(menus) {
  notify.store.set(MENU_SEEN_KEY, String(newestMenu(menus)));
  $("#menu-badge").hidden = true;
}

export async function refreshMenuBadge() {
  try {
    const seen = Number(notify.store.get(MENU_SEEN_KEY) || 0);
    const current = (await api("/menus")).filter((menu) => menu.status !== "over");
    $("#menu-badge").hidden = newestMenu(current) <= seen;
  } catch {
    // 印が出せなくても 続ける
  }
}

async function loadCatalog() {
  [catalog, menuList] = await Promise.all([api("/exercises"), api("/menus").catch(() => [])]);
}

export async function renderWorkout(view) {
  clearInterval(clockTimer);
  const { workout } = await api("/workouts/active");
  if (!workout) return renderStart(view);
  await loadCatalog();
  renderActive(view, workout);
}

const isoDate = (date) => `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;

async function renderStart(view) {
  const [body, menus] = await Promise.all([
    api("/body").catch(() => ({ logs: [] })),
    api("/menus").catch(() => []),
  ]);
  markMenusSeen(menus);
  const weights = body.logs.filter((log) => log.weight != null);
  const lastWeight = weights.length ? weights[weights.length - 1].weight : null; // 前に入れた体重を うすく出しておく
  const today = new Date();
  const oldest = new Date(today.getTime() - BACKFILL_DAYS * 86400000);
  const yesterday = new Date(today.getTime() - 86400000);

  // 期間がすぎたメニューは ここには出さず、「過去のメニュー」から見る
  const current = menus.filter((menu) => menu.status !== "over");
  const past = menus.filter((menu) => menu.status === "over");
  const menuCards = current.map((menu) => `
    <div class="menu-card">
      <strong>${icon("menu")}${esc(menu.title)}</strong>
      ${menuPeriodHtml(menu)}
      ${menu.note ? `<p class="menu-note">${esc(menu.note)}</p>` : ""}
      ${menuPhotosHtml(menu)}
      ${menu.items.length ? `<ul class="menu-items">${menu.items.map((item) =>
        `<li><span>${esc(item.name)}</span><span>${esc(item.label)}</span></li>`).join("")}</ul>` : ""}
      <button type="button" class="dark wide" data-menu="${menu.id}">このメニューで始める</button>
    </div>`).join("");

  view.innerHTML = `
    <div class="card center start-card">
      <p>今日の筋トレを 記録しよう</p>
      <label class="start-weight">筋トレ前の体重
        <input id="body-weight" type="number" inputmode="decimal" min="20" max="300" step="0.1" placeholder="${lastWeight ?? ""}"> kg</label>
      <button type="button" id="start" class="primary big">筋トレ開始！</button>
    </div>
    ${current.length ? `<div class="card"><strong>トレーナーからのメニュー</strong>${menuCards}</div>` : ""}
    ${past.length ? `<button type="button" id="past-menus" class="link center-block">過去のメニューを見る（${past.length}件）</button>` : ""}
    <details class="card backfill">
      <summary>あとから記録する（${BACKFILL_DAYS}日前まで）</summary>
      <p class="hint">記録し忘れた筋トレを、あとから入れられます。休憩タイマーは 動きません</p>
      <div class="inputs">
        <label>日 <input id="backfill-day" type="date" value="${isoDate(yesterday)}" min="${isoDate(oldest)}" max="${isoDate(today)}"></label>
        <label>時間 <input id="backfill-minutes" type="number" inputmode="numeric" min="1" max="300" step="5" value="60"> 分</label>
      </div>
      <button type="button" id="backfill-start" class="dark wide">あとから記録を始める</button>
    </details>`;

  const begin = (button, payload) => run(button, async () => {
    notify.prepareAudio();
    const data = await api("/workouts/start", { method: "POST", body: payload });
    await loadCatalog();
    renderActive.lastInput = null;
    renderActive(view, data.workout);
  });
  const weight = () => {
    const value = $("#body-weight", view).value;
    return value === "" ? null : Number(value);
  };
  $("#start", view).addEventListener("click", (event) => begin(event.currentTarget, { body_weight: weight() }));
  $("#past-menus", view)?.addEventListener("click", () => openMenus(menus, { onlyPast: true }));
  view.querySelectorAll("[data-menu]").forEach((button) => button.addEventListener("click", () =>
    begin(button, { body_weight: weight(), menu_id: Number(button.dataset.menu) })));
  $("#backfill-start", view).addEventListener("click", (event) => {
    const day = $("#backfill-day", view).value;
    if (!day) return toast("日を選んでね");
    begin(event.currentTarget, { day, minutes: Number($("#backfill-minutes", view).value) || 60 });
  });
}

function elapsed(workout) {
  const minutes = Math.max(0, Math.floor((Date.now() - new Date(workout.started_at)) / 60000));
  return minutes >= 60 ? `${Math.floor(minutes / 60)}時間${minutes % 60}分` : `${minutes}分`;
}

// メニューの目標を、入れる欄の値に（有酸素は 目標の名前が ちがう）
function targetValues(target, last) {
  if (!target) return null;
  if (target.kind === "cardio" || target.kind === "minutes") return { duration_min: target.minutes, distance_km: target.distance_km };
  return { weight: target.weight ?? last?.weight, reps: target.reps || null, seconds: target.seconds, distance_m: target.distance_m };
}

// 配られたメニュー：「今日の記録」の上に出す。見出しを押すと 小さくたためる。
// このメニューで始めたときは、種目ごとに 目標と 今日やったセット数も出す（押すと その種目を選ぶ）
function menuHtml(workout) {
  const menu = workout.menu;
  const others = menuList.filter((m) => m.status !== "over" && m.id !== menu?.id);
  const past = menuList.filter((m) => m.status === "over" && m.id !== menu?.id);
  if (!menu && !others.length && !past.length) return "";
  const rows = (menu?.items ?? []).map((item) => {
    const done = workout.exercises.find((e) => e.exercise_id === item.exercise_id)?.count ?? 0;
    const finished = done >= item.sets;
    return `<button type="button" class="menu-row${finished ? " done" : ""}${item.exercise_id === selectedExercise ? " on" : ""}"
      data-menu-exercise="${item.exercise_id}" data-part="${item.part}">
      <span class="menu-check">${finished ? "✓" : ""}</span>
      <span class="grow"><strong>${esc(item.name)}</strong><span class="hint">${esc(item.label)}</span></span>
      <span class="menu-progress">${done} / ${item.sets}</span></button>`;
  }).join("");
  const mine = menu ? `${menuPeriodHtml(menu)}
    ${menu.note ? `<p class="menu-note">${esc(menu.note)}</p>` : ""}
    ${menu.photos.length ? `${menuPhotosHtml(menu)}<p class="hint">写真を押すと 大きく見られます</p>` : ""}
    ${rows ? `<div class="menu-rows">${rows}</div>` : ""}` : "";
  // ほかのメニューは 名前だけ並べて、押すと開く（メニューを選ばずに始めて、1つしかないときは 最初から開く）
  const folds = others.map((m) => `<details class="menu-fold"${!menu && others.length === 1 ? " open" : ""}>
    <summary><strong>${icon("menu")}${esc(m.title)}</strong></summary>${menuDetailHtml(m)}</details>`).join("");
  return `<details class="card menu-box" id="menu-box"${menuOpen ? " open" : ""}>
    <summary><strong>${icon("menu")}${menu ? esc(menu.title) : "トレーナーからのメニュー"}</strong></summary>
    ${mine}${folds}
    ${past.length ? `<button type="button" id="past-menus" class="link">過去のメニューを見る（${past.length}件）</button>` : ""}
  </details>`;
}

// ---- 記録の一覧（筋トレ中と、投稿する前の画面で使う）：セットを直す・消す、種目ごとのメモ ----

export function recordListHtml(workout) {
  if (!workout.exercises.length) return '<p class="hint">まだありません</p>';
  return workout.exercises.map((e) => `
    <div class="set-group record" data-ex="${e.exercise_id}" data-kind="${e.kind}">
      <div class="record-head"><strong>${esc(e.name)}</strong>
        <button type="button" class="link" data-note-open>${icon("note")}${e.note ? "メモを直す" : "メモ"}</button></div>
      <ol>${e.sets.map((s) => `<li data-set="${s.id}"><span class="set-text">${esc(s.label)}${s.is_pr ? ' <span class="pr">★自己ベスト</span>' : ""}</span>
        <span class="set-actions"><button type="button" class="link" data-edit>直す</button><button type="button" class="link" data-del>消す</button></span></li>`).join("")}</ol>
      ${e.note ? `<p class="ex-note">${esc(e.note)}</p>` : ""}
      <div class="note-form" hidden>
        <input maxlength="200" value="${esc(e.note)}" placeholder="この種目のメモ（例：右肩が少し痛い）">
        <button type="button" class="small dark" data-note-save>保存</button>
      </div>
    </div>`).join("");
}

// changed(workout)：直したあとの 新しい筋トレで 描き直す
export function bindRecordList(root, getWorkout, changed) {
  root.addEventListener("click", (event) => {
    const button = event.target.closest("button");
    if (!button || !root.contains(button)) return;
    const group = button.closest("[data-ex]");
    const item = button.closest("[data-set]");
    const workout = getWorkout();
    if (button.matches("[data-del]")) {
      return run(button, async () => changed((await api(`/workouts/sets/${item.dataset.set}`, { method: "DELETE" })).workout));
    }
    if (button.matches("[data-edit]")) {
      const entry = workout.exercises.find((e) => e.exercise_id === Number(group.dataset.ex));
      const set = entry.sets.find((s) => s.id === Number(item.dataset.set));
      item.innerHTML = `<div class="inputs edit-inputs">${kindFieldsHtml(entry.kind, set)}</div>
        <span class="set-actions"><button type="button" class="small dark" data-save>保存</button><button type="button" class="link" data-cancel>やめる</button></span>`;
      item.querySelector("input")?.focus();
      return;
    }
    if (button.matches("[data-save]")) {
      return run(button, async () => changed((await api(`/workouts/sets/${item.dataset.set}`, { method: "PATCH", body: readKindFields(item) })).workout));
    }
    if (button.matches("[data-cancel]")) return changed(workout);
    if (button.matches("[data-note-open]")) {
      const form = group.querySelector(".note-form");
      form.hidden = !form.hidden;
      if (!form.hidden) form.querySelector("input").focus();
      return;
    }
    if (button.matches("[data-note-save]")) {
      const body = group.querySelector(".note-form input").value;
      return run(button, async () => changed((await api(`/workouts/${workout.id}/notes/${group.dataset.ex}`, { method: "PUT", body: { body } })).workout));
    }
  });
}

// 種目を足す（名前・記録の仕方）。有酸素の欄では 記録の仕方は 選ばない
function openNewExercise(part, done) {
  const kinds = Object.entries(state.kinds ?? {}).filter(([key]) => key !== "cardio");
  const sheet = openSheet(`「${state.parts[part]}」に種目を足す`, `
    <form id="new-exercise-form" class="form">
      <label>種目の名前<input name="name" maxlength="30" required placeholder="例：ケーブルクロス"></label>
      ${part === "cardio" ? "" : `<label>記録の仕方<select name="kind">${kinds.map(([key, label]) =>
        `<option value="${key}">${esc(label)}</option>`).join("")}</select></label>`}
      <p class="hint">チームのみんなが 使えるようになります</p>
      <button type="submit" class="primary wide">足す</button>
    </form>`);
  const form = $("#new-exercise-form", sheet);
  form.elements.name.focus();
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    run(form.querySelector("button[type=submit]"), async () => {
      const created = await api("/exercises", {
        method: "POST", body: { name: form.elements.name.value.trim(), part, kind: form.elements.kind?.value },
      });
      closeSheet();
      await done(created.id);
    });
  });
}

function renderActive(view, workout) {
  const inPart = () => catalog.exercises.filter((e) => e.part === selectedPart);
  if (!(selectedPart in state.parts)) selectedPart = "chest";
  if (!inPart().some((e) => e.id === selectedExercise)) {
    // メニューで始めたときは、最初に メニューの 1つ目の種目を選んでおく
    const first = workout.menu?.items.find((item) => !workout.exercises.some((e) => e.exercise_id === item.exercise_id && e.count >= item.sets));
    if (first && selectedExercise === null && !workout.exercises.length) {
      selectedPart = first.part;
      selectedExercise = first.exercise_id;
    } else {
      selectedExercise = inPart()[0]?.id ?? null;
    }
  }
  const exercise = catalog.exercises.find((e) => e.id === selectedExercise);
  const kind = exercise?.kind ?? "weight_reps";
  const last = exercise?.last ?? [];
  // 入れる数字の 最初の値：今日 この種目をやっていれば 最後に入れた数字、なければ メニューの目標、それもなければ 前回の 1セット目
  const today = workout.exercises.find((e) => e.exercise_id === selectedExercise);
  const target = workout.menu?.items.find((item) => item.exercise_id === selectedExercise);
  const hint = today ? null : targetValues(target, last[0]) ?? last[0];

  const partChips = Object.entries(state.parts).map(([key, label]) =>
    `<button type="button" class="chip${key === selectedPart ? " on" : ""}${key === "sport" ? " sport" : ""}" data-part-chip="${key}">${esc(label)}</button>`).join("");
  const options = inPart().map((e) =>
    `<option value="${e.id}"${e.id === selectedExercise ? " selected" : ""}>${esc(e.name)}</option>`).join("");

  const status = workout.backfill
    ? `<strong>あとから記録</strong><span class="hint">${formatDay(workout.day)}・${workout.planned_minutes}分</span>`
    : `<strong>筋トレ中</strong><span id="elapsed" class="hint">${elapsed(workout)}</span>`;

  view.innerHTML = `
    <div class="card">
      <div class="row between">${status}</div>
      <div class="chips">${partChips}</div>
      <div class="row">
        <select id="exercise" class="grow"${options ? "" : " disabled"}>${options || '<option>この欄の種目は まだありません</option>'}</select>
        <button type="button" id="new-exercise" class="small">＋種目</button>
      </div>
      <p class="hint">${target ? `目標：${esc(target.label)}　` : ""}${last.length ? `前回：${esc(last.map((s) => s.label).join("、"))}` : "前回：記録なし"}</p>
      <div class="inputs add-inputs">${kindFieldsHtml(kind, hint, { withIds: true })}</div>
      <button type="button" id="add" class="primary wide"${exercise ? "" : " disabled"}>追加</button>
    </div>
    ${menuHtml(workout)}
    <div class="card">
      <strong>${workout.backfill ? "この日の記録" : "今日の記録"}</strong>
      <div id="record">${recordListHtml(workout)}</div>
    </div>
    <button type="button" id="finish" class="dark wide">${workout.backfill ? "記録を終わる" : "筋トレを終わる"}</button>
    <button type="button" id="cancel" class="link center-block">この筋トレを取り消す</button>`;

  // 今日 この種目をやっていれば、最後に入れた数字を そのまま残す
  if (today && renderActive.lastInput?.exerciseId === selectedExercise) {
    for (const [field, value] of Object.entries(renderActive.lastInput.values)) {
      const input = $(`.add-inputs [data-field="${field}"]`, view);
      if (input && value != null) input.value = value;
    }
  }

  clearInterval(clockTimer);
  if (!workout.backfill) {
    clockTimer = setInterval(() => {
      const label = $("#elapsed");
      if (label) label.textContent = elapsed(workout);
      else clearInterval(clockTimer);
    }, 15000);
  }

  view.querySelectorAll("[data-part-chip]").forEach((chip) => chip.addEventListener("click", () => {
    selectedPart = chip.dataset.partChip;
    selectedExercise = inPart()[0]?.id ?? null;
    renderActive(view, workout);
  }));
  view.querySelectorAll("[data-menu-exercise]").forEach((row) => row.addEventListener("click", () => {
    selectedPart = row.dataset.part;
    selectedExercise = Number(row.dataset.menuExercise);
    renderActive(view, workout);
  }));
  // メニューを たたんだか 開いたかを 覚えておく（セットを追加して 画面を描き直しても、そのままにする）
  $("#menu-box", view)?.addEventListener("toggle", (event) => (menuOpen = event.currentTarget.open));
  $("#past-menus", view)?.addEventListener("click", () => openMenus(menuList, { onlyPast: true }));
  $("#exercise", view).addEventListener("change", (event) => {
    selectedExercise = Number(event.target.value);
    renderActive(view, workout);
  });
  $("#new-exercise", view).addEventListener("click", () => openNewExercise(selectedPart, async (id) => {
    await loadCatalog();
    selectedExercise = id;
    renderActive(view, workout);
  }));

  $("#add", view).addEventListener("click", (event) => run(event.currentTarget, async () => {
    notify.prepareAudio(); // 押したときに 音の用意をしておく（あとで鳴らせるように）
    const values = readKindFields($(".add-inputs", view));
    const data = await api(`/workouts/${workout.id}/sets`, { method: "POST", body: { exercise_id: exercise.id, ...values } });
    renderActive.lastInput = { exerciseId: exercise.id, values };
    if (data.is_pr) toast("★ 自己ベスト更新！");
    renderActive(view, data.workout);
    if (kind !== "cardio" && !workout.backfill) startRest(exercise, catalog.rest); // あとから記録では 休憩タイマーを動かさない
  }));

  bindRecordList($("#record", view), () => workout, (fresh) => renderActive(view, fresh));

  const leave = () => {
    clearInterval(clockTimer);
    stopRest();
    renderActive.lastInput = null;
    selectedExercise = null;
    menuOpen = false;
  };
  $("#finish", view).addEventListener("click", (event) => run(event.currentTarget, async () => {
    const data = await api(`/workouts/${workout.id}/finish`, { method: "POST" });
    leave();
    renderCompose(view, data.workout);
  }));
  $("#cancel", view).addEventListener("click", (event) => {
    if (!confirm("この筋トレを取り消しますか？（入れたセットも 消えます）")) return;
    run(event.currentTarget, async () => {
      await api(`/workouts/${workout.id}`, { method: "DELETE" });
      leave();
      await renderStart(view);
    });
  });
}

// 投稿の画面（筋トレを終わった直後。あとから 自分のページからも開ける）
// 投稿する前なら、セットの数字を直したり、種目ごとのメモを付けたりできる
export function renderCompose(view, workout) {
  const max = state.limits.photos;
  let photos = [];
  view.innerHTML = `
    <div class="card">
      <strong>おつかれさま！ タイムラインに投稿しよう</strong>
      <p class="hint">記録を直すなら 今のうちに（投稿すると セットは直せません）。種目ごとに メモも付けられます</p>
      <div id="record">${recordListHtml(workout)}</div>
      <textarea id="body" rows="3" maxlength="${state.limits.body}" placeholder="全体の ひとこと（なくてもOK）"></textarea>
      <div id="thumbs" class="thumbs"></div>
      <label class="button small file">${icon("camera")}写真を足す（${max}枚まで）
        <input id="photo" type="file" accept="image/*" multiple hidden></label>
    </div>
    <button type="button" id="post" class="primary wide">投稿する</button>
    <button type="button" id="skip" class="link center-block">投稿しない</button>
    <p class="hint center">写真は ${Math.round(state.limits.photo_keep_days / 30)}か月たつと 自動で消えます（記録・ひとこと・コメントは 残ります）</p>
    <p class="hint center">投稿しなくても、記録は 自分のページに残ります</p>`;

  // 記録の一覧だけ 描き直す（書きかけの ひとこと・写真は そのまま）
  bindRecordList($("#record", view), () => workout, (fresh) => {
    workout = fresh;
    $("#record", view).innerHTML = recordListHtml(workout);
  });

  const drawThumbs = () => {
    $("#thumbs", view).innerHTML = photos.map((src, index) =>
      `<span class="thumb"><img src="${src}" alt=""><button type="button" data-remove="${index}" aria-label="この写真を外す">×</button></span>`).join("");
    view.querySelectorAll("[data-remove]").forEach((button) => button.addEventListener("click", () => {
      photos.splice(Number(button.dataset.remove), 1);
      drawThumbs();
    }));
  };
  $("#photo", view).addEventListener("change", async (event) => {
    const files = [...event.target.files];
    event.target.value = "";
    for (const file of files) {
      if (photos.length >= max) {
        toast(`写真は ${max}枚までです`);
        break;
      }
      try {
        photos.push(await shrinkPhoto(file));
      } catch (error) {
        toast(error.message);
      }
    }
    drawThumbs();
  });
  $("#post", view).addEventListener("click", (event) => run(event.currentTarget, async () => {
    if (!workout.exercises.length) return toast("セットが 1つもありません");
    await api("/posts", { method: "POST", body: { workout_id: workout.id, body: $("#body", view).value, photos } });
    photos = [];
    toast("投稿しました");
    state.go("timeline");
  }));
  $("#skip", view).addEventListener("click", () => state.go("me"));
}

