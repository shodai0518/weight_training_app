// トレーナー用の管理画面（受信箱・メニュー・チームの管理）

import {
  $, api, avatar, closeSheet, esc, groupByPosition, positionTag, formatDay, formatTime, icon, kindFieldsHtml, menuPeriodHtml, menuPhotosHtml, openSheet, pad,
  readKindFields, run, shrinkPhoto, state, toast,
} from "./api.js";
import { bindPosts, parts, postBody, postHead } from "./timeline.js";

let tab = "team"; // 「管理」を開いたときは チームの管理から
let membersOpen = false; // チームの管理の「メンバー」の欄を 開いているか（最初は たたんでおく。開いたら そのまま）
const openPositions = new Set(); // 「メンバー」の中で 開いている ポジション
const openMembers = new Set(); // 操作のボタンを 開いている人
const TABS = { team: "チームの管理", inbox: "受信箱", menus: "メニュー" };

export async function renderAdmin(view) {
  view.innerHTML = `
    <div class="filter">${Object.entries(TABS).map(([key, label]) =>
      `<button type="button" data-admin="${key}" class="${tab === key ? "on" : ""}">${label}</button>`).join("")}</div>
    <div id="admin-body"></div>`;
  view.querySelectorAll("[data-admin]").forEach((button) => button.addEventListener("click", () => {
    tab = button.dataset.admin;
    renderAdmin(view);
  }));
  const body = $("#admin-body", view);
  if (tab === "inbox") await renderInbox(body);
  else if (tab === "menus") await renderMenus(body);
  else await renderTeam(body);
}

// ---- 受信箱：まだ見ていない投稿を 一覧で出す（名前・投稿した時間・ひとこと）。押すと 中身が開く ----

async function renderInbox(body, { openFirst = false } = {}) {
  const [inbox, replies] = await Promise.all([api("/admin/inbox"), api("/admin/quick-replies")]);
  setInboxCount(inbox.total);
  if (!inbox.total) {
    closeSheet();
    body.innerHTML = '<p class="hint center">まだ見ていない投稿は ありません 👍</p>';
    return;
  }
  body.innerHTML = `<p class="inbox-count">まだ見ていない投稿　あと <strong>${inbox.total}</strong> 件</p>
    <div class="card list">${inbox.posts.map((post) => `
      <button type="button" class="list-row inbox-row" data-review="${post.id}">
        <span class="list-main"><span class="who-line">${avatar(post.user)}<strong>${esc(post.user.name)}</strong>${positionTag(post.user)}</span><time>${formatTime(post.created_at)}</time></span>
        <span class="row-body${post.body ? "" : " empty"}">${post.body ? esc(post.body) : "（ひとこと なし）"}</span>
      </button>`).join("")}</div>`;
  const open = (post) => openReview(body, post, replies);
  body.querySelectorAll("[data-review]").forEach((row) => row.addEventListener("click", () =>
    open(inbox.posts.find((post) => post.id === Number(row.dataset.review)))));
  if (openFirst) open(inbox.posts[0]); // 「送って次へ」：次の投稿を そのまま開く
}

function reviewHtml(post, replies) {
  const quick = replies.map((r) => `<button type="button" class="chip" data-quick="${esc(r.body)}">${esc(r.body)}</button>`).join("");

  return `<article class="post detail review" data-post="${post.id}">
    ${postHead(post)}
    ${postBody(post, { trend: true, open: true })}
    ${parts.reactionsHtml(post.reactions, "posts", post.id)}
    <strong class="comments-title">コメント ${post.comment_count}件</strong>
    <div class="comments">${parts.commentsHtml(post.comments) || '<p class="hint">まだコメントは ありません</p>'}</div>
    <div class="review-bar">
      ${parts.stampBarHtml(post)}
      <div class="chips">${quick}</div>
      ${parts.replyingHtml}
      <textarea class="reply" rows="2" maxlength="${state.limits.comment}" placeholder="コメントを書く…"></textarea>
      <div class="row">
        <button type="button" class="small grow" data-seen>見ただけ</button>
        <button type="button" class="primary grow" data-send>送って次へ</button>
      </div>
    </div>
  </article>`;
}

// 1つの投稿の中身（写真・全部のセット・前回とのちがい・コメント）を 下から出す
function openReview(body, post, replies) {
  const box = openSheet("投稿を見る", '<div class="detail-box"></div>').firstElementChild;
  const draw = () => (box.innerHTML = reviewHtml(post, replies));
  draw();
  const next = () => renderInbox(body, { openFirst: true });
  bindPosts(box, {
    // スタンプなどで返ってくる投稿には「前回とのちがい」が付いていないので、筋トレの部分は 前のものを残す
    refresh: (card, fresh) => {
      const draft = $(".reply", box)?.value ?? "";
      Object.assign(post, fresh, { workout: post.workout });
      draw();
      $(".reply", box).value = draft;
    },
    removed: () => {
      closeSheet();
      renderInbox(body);
    },
  });
  box.addEventListener("click", (event) => {
    const card = $("[data-post]", box);
    const reply = $(".reply", box);
    const quick = event.target.closest("[data-quick]");
    if (quick) {
      reply.value = reply.value ? `${reply.value} ${quick.dataset.quick}` : quick.dataset.quick;
      reply.focus();
      return;
    }
    const seen = event.target.closest("[data-seen]");
    const send = event.target.closest("[data-send]");
    if (seen) {
      run(seen, async () => {
        await api(`/admin/posts/${post.id}/review`, { method: "POST" });
        await next();
      });
    } else if (send) {
      const text = reply.value.trim();
      if (!text) return toast("コメントを書いてね（書かないときは「見ただけ」）");
      run(send, async () => {
        const parent_id = card.dataset.replyTo ? Number(card.dataset.replyTo) : null;
        await api(`/posts/${post.id}/comments`, { method: "POST", body: { body: text, parent_id } });
        toast("送りました");
        await next();
      });
    }
  });
}

export function setInboxCount(count) {
  const badge = $("#inbox-count");
  badge.hidden = !count;
  badge.textContent = count > 99 ? "99+" : count;
}

export async function refreshInboxCount() {
  if (state.me?.role !== "trainer") return;
  try {
    setInboxCount((await api("/admin/inbox")).total);
  } catch {
    // 数が出せなくても 続ける
  }
}

// ---- メニュー：トレーナーが作って配る。配られた人は「記録」の画面で それを開いて始める ----

let draftItems = []; // 作りかけのメニューの種目
let draftPhotos = []; // 作りかけのメニューの写真
let draftTargets = null; // 配る相手（null なら チーム全員）
const MENU_MAX_PHOTOS = 4; // サーバーと同じ数字
let draftPart = "chest";

// 種目を選んだときの 目標の 最初の値
const DEFAULT_TARGET = {
  weight_reps: { reps: 10 }, reps: { reps: 10 }, count: { reps: 10 }, time: { seconds: 60 }, minutes: { duration_min: 15 },
  distance_sets: { distance_m: 40, reps: 10 }, cardio: { duration_min: 30 },
};

// メニューの目標の書き方（サーバーと同じ）
function targetLabel(item, kind) {
  if (kind === "cardio") return [item.minutes ? `${item.minutes}分` : "", item.distance_km ? `${item.distance_km}km` : ""].filter(Boolean).join("・") || "自由";
  const core = kind === "time" ? `${item.seconds}秒`
    : kind === "minutes" ? `${item.minutes}分`
    : kind === "count" ? `${item.reps}本`
    : kind === "distance_sets" ? `${item.distance_m}m×${item.reps}本`
    : kind === "reps" || !item.weight ? `${item.reps}回` : `${item.weight}kg×${item.reps}回`;
  return `${core}×${item.sets}セット`;
}

async function renderMenus(body) {
  const [menus, catalog, team] = await Promise.all([api("/admin/menus"), api("/exercises"), api("/admin/team")]);
  // 期間の 最初の値：今週の 月曜から 日曜
  const iso = (date) => `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
  const monday = new Date();
  monday.setDate(monday.getDate() - ((monday.getDay() + 6) % 7));
  const sunday = new Date(monday.getTime() + 6 * 86400000);
  const exercises = catalog.exercises;
  const byId = Object.fromEntries(exercises.map((e) => [e.id, e]));
  if (!(draftPart in state.parts)) draftPart = "chest";
  const partOptions = Object.entries(state.parts).map(([key, label]) =>
    `<option value="${key}"${key === draftPart ? " selected" : ""}>${esc(label)}</option>`).join("");
  const people = team.users.filter((u) => u.active && !u.me);

  const listed = menus.map((menu) => `
    <div class="menu-card">
      <strong>${icon("menu")}${esc(menu.title)}</strong>
      ${menuPeriodHtml(menu)}
      <p class="menu-to">${menu.targets.length ? `配る相手：${esc(menu.targets.map((t) => t.name).join("、"))}（${menu.targets.length}人）` : "配る相手：チーム全員"}</p>
      ${menu.note ? `<p class="menu-note">${esc(menu.note)}</p>` : ""}
      ${menuPhotosHtml(menu, true)}
      <ul class="menu-items">${menu.items.map((item) => `<li><span>${esc(item.name)}</span><span>${esc(item.label)}</span></li>`).join("")}</ul>
      <button type="button" class="small" data-stop="${menu.id}">配るのをやめる</button>
    </div>`).join("");

  body.innerHTML = `
    <div class="card">
      <strong>メニューを作る</strong>
      <p class="hint">配ると、相手の「記録」の画面に出て、通知も届きます。期間がすぎると「過去のメニュー」に移り、半年たつと 消えます</p>
      <form id="menu-form" class="form">
        <label>メニューの名前<input name="title" maxlength="40" required placeholder="例：今週の上半身"></label>
        <div class="period">
          <label>いつから<input name="start_day" type="date" value="${iso(monday)}"></label>
          <label>いつまで<input name="end_day" type="date" value="${iso(sunday)}"></label>
        </div>
        <label>コメント<textarea name="note" rows="3" maxlength="300" placeholder="例：フォーム重視で。重さは無理しない"></textarea></label>
      </form>
      <div class="targets">
        <p class="field-title">配る相手</p>
        <div class="filter small-filter">
          <button type="button" data-target-mode="all" class="${draftTargets ? "" : "on"}">チーム全員</button>
          <button type="button" data-target-mode="pick" class="${draftTargets ? "on" : ""}">人を選ぶ</button>
        </div>
        <div id="target-list"${draftTargets ? "" : " hidden"}>
          <p class="hint">ポジションを押すと、そのポジションの全員を 選んで 開きます（はずしたい人は チェックを外す。もう一度押すと まとめて外れます）</p>
          ${groupByPosition(people).map((g) => `<details class="target-group" data-group="${esc(g.label)}"${g.members.some((u) => draftTargets?.includes(u.id)) ? " open" : ""}>
            <summary class="target-head"><strong>${esc(g.label)}</strong><span class="target-count"></span></summary>
            <div class="target-list">${g.members.map((u) => `
              <label class="check"><input type="checkbox" value="${u.id}" data-group="${esc(g.label)}"${draftTargets?.includes(u.id) ? " checked" : ""}>
                ${avatar(u)}<span>${esc(u.name)}</span>${u.role === "trainer" ? '<span class="role">トレーナー</span>' : ""}</label>`).join("")}</div>
          </details>`).join("") || '<p class="hint">まだ メンバーが いません</p>'}
        </div>
      </div>
      <div id="menu-thumbs" class="thumbs"></div>
      <label class="button small file">${icon("camera")}メニューの写真を足す（${MENU_MAX_PHOTOS}枚まで）
        <input id="menu-photo" type="file" accept="image/*" multiple hidden></label>
      <p class="hint builder-title">種目を入れる（なくてもOK）：入れると、記録するときに 目標の数字が最初から入り、何セットやったかが出ます</p>
      <ul id="draft" class="menu-items draft"></ul>
      <div class="menu-builder">
        <div class="row">
          <select id="menu-part">${partOptions}</select>
          <select id="menu-exercise" class="grow"></select>
        </div>
        <div id="menu-fields" class="inputs three"></div>
        <button type="button" id="menu-add" class="small wide">この種目を足す</button>
      </div>
      <button type="button" id="menu-publish" class="primary wide">メニューを配る</button>
    </div>
    <div class="card"><strong>今 配っているメニュー</strong>${listed || '<p class="hint">まだありません</p>'}</div>`;

  const drawDraft = () => {
    $("#draft", body).innerHTML = draftItems.map((item, index) =>
      `<li><span>${esc(byId[item.exercise_id]?.name ?? "？")}</span><span>${esc(targetLabel(item, byId[item.exercise_id]?.kind))}
        <button type="button" class="link" data-drop="${index}">外す</button></span></li>`).join("");
    body.querySelectorAll("[data-drop]").forEach((button) => button.addEventListener("click", () => {
      draftItems.splice(Number(button.dataset.drop), 1);
      drawDraft();
    }));
  };
  // 選んだ種目の 記録の仕方に合わせて、目標を入れる欄を変える（有酸素は セット数なし）
  const drawFields = () => {
    const kind = byId[Number($("#menu-exercise", body).value)]?.kind ?? "weight_reps";
    $("#menu-fields", body).innerHTML = kindFieldsHtml(kind, DEFAULT_TARGET[kind]) +
      (kind === "cardio" ? "" : '<label>セット <input data-sets type="number" inputmode="numeric" min="1" max="20" value="3"></label>');
    $('#menu-fields [data-field="weight"]', body)?.setAttribute("placeholder", "自由");
  };
  const drawExercises = () => {
    const options = exercises.filter((e) => e.part === draftPart).map((e) => `<option value="${e.id}">${esc(e.name)}</option>`).join("");
    $("#menu-exercise", body).innerHTML = options || "<option value=\"\">この欄の種目は まだありません</option>";
    drawFields();
  };
  const drawPhotos = () => {
    $("#menu-thumbs", body).innerHTML = draftPhotos.map((src, index) =>
      `<span class="thumb"><img src="${src}" alt=""><button type="button" data-unphoto="${index}" aria-label="この写真を外す">×</button></span>`).join("");
    body.querySelectorAll("[data-unphoto]").forEach((button) => button.addEventListener("click", () => {
      draftPhotos.splice(Number(button.dataset.unphoto), 1);
      drawPhotos();
    }));
  };
  $("#menu-photo", body).addEventListener("change", async (event) => {
    const files = [...event.target.files];
    event.target.value = "";
    for (const file of files) {
      if (draftPhotos.length >= MENU_MAX_PHOTOS) {
        toast(`写真は ${MENU_MAX_PHOTOS}枚までです`);
        break;
      }
      try {
        draftPhotos.push(await shrinkPhoto(file));
      } catch (error) {
        toast(error.message);
      }
    }
    drawPhotos();
  });
  drawDraft();
  drawPhotos();
  drawExercises();

  // 配る相手
  const readTargets = () => [...body.querySelectorAll("#target-list input:checked")].map((input) => Number(input.value));
  body.querySelectorAll("[data-target-mode]").forEach((button) => button.addEventListener("click", () => {
    const pick = button.dataset.targetMode === "pick";
    draftTargets = pick ? readTargets() : null;
    body.querySelectorAll("[data-target-mode]").forEach((b) => b.classList.toggle("on", b === button));
    $("#target-list", body).hidden = !pick;
  }));
  $("#target-list", body).addEventListener("change", () => (draftTargets = readTargets()));
  // ポジションごとに たたんでおく。押すと そのポジションの全員を 選んで 開く。開いているときに押すと、まとめて外して たたむ
  const markGroups = () => body.querySelectorAll(".target-group").forEach((group) => {
    const boxes = [...group.querySelectorAll("input[type=checkbox]")];
    const picked = boxes.filter((b) => b.checked).length;
    group.querySelector(".target-count").textContent = picked ? `${picked} / ${boxes.length}人 選んでいます` : `${boxes.length}人`;
    group.classList.toggle("picked", picked > 0);
  });
  body.querySelectorAll(".target-group > summary").forEach((summary) => summary.addEventListener("click", (event) => {
    event.preventDefault(); // 開け閉めは ここで決める
    const group = summary.parentElement;
    const open = !group.open;
    group.querySelectorAll("input[type=checkbox]").forEach((b) => (b.checked = open));
    group.open = open;
    draftTargets = readTargets();
    markGroups();
  }));
  $("#target-list", body).addEventListener("change", markGroups);
  markGroups();

  $("#menu-part", body).addEventListener("change", (event) => {
    draftPart = event.target.value;
    drawExercises();
  });
  $("#menu-exercise", body).addEventListener("change", drawFields);
  $("#menu-add", body).addEventListener("click", () => {
    const exercise = byId[Number($("#menu-exercise", body).value)];
    if (!exercise) return toast("種目を選んでね");
    const values = readKindFields($("#menu-fields", body));
    const sets = Number($("#menu-fields [data-sets]", body)?.value || 1);
    const item = exercise.kind === "cardio"
      ? { exercise_id: exercise.id, minutes: values.duration_min, distance_km: values.distance_km, sets: 1 }
      : { exercise_id: exercise.id, sets, weight: values.weight ?? null, reps: values.reps ?? null, seconds: values.seconds ?? null,
        distance_m: values.distance_m ?? null, minutes: values.duration_min ?? null };
    const missing = exercise.kind === "cardio" ? !(item.minutes || item.distance_km)
      : !(sets >= 1) || (exercise.kind === "time" ? !item.seconds : exercise.kind === "minutes" ? !item.minutes
        : exercise.kind === "distance_sets" ? !(item.distance_m && item.reps) : !item.reps);
    if (missing) return toast("目標の数字を入れてね");
    draftItems.push(item);
    drawDraft();
  });
  $("#menu-publish", body).addEventListener("click", (event) => {
    const form = $("#menu-form", body);
    if (!form.reportValidity()) return;
    if (!draftItems.length && !draftPhotos.length) return toast("写真か 種目を 足してね");
    if (draftTargets && !draftTargets.length) return toast("配る相手を 選んでね");
    run(event.currentTarget, async () => {
      await api("/admin/menus", {
        method: "POST",
        body: {
          title: form.elements.title.value,
          note: form.elements.note.value,
          start_day: form.elements.start_day.value || null,
          end_day: form.elements.end_day.value || null,
          items: draftItems,
          photos: draftPhotos,
          targets: draftTargets ?? [],
        },
      });
      const who = draftTargets ? `${draftTargets.length}人` : "みんな";
      draftItems = [];
      draftPhotos = [];
      draftTargets = null;
      toast(`メニューを配りました。${who}の「タイムライン」の上と「記録」の画面に出ます`);
      await renderMenus(body);
    });
  });
  body.querySelectorAll("[data-stop]").forEach((button) => button.addEventListener("click", () => {
    if (!confirm("このメニューを配るのを やめますか？（このメニューでやった記録は 残ります）")) return;
    run(button, async () => {
      await api(`/admin/menus/${button.dataset.stop}`, { method: "DELETE" });
      await renderMenus(body);
    });
  }));
}

// ---- チームの管理 ----

async function renderTeam(body) {
  const [team, replies, owned, catalog] = await Promise.all([
    api("/admin/team"), api("/admin/quick-replies"), api("/admin/exercises"), api("/exercises"),
  ]);
  const partName = (part) => state.parts[part] ?? "競技";
  // まとめ先の候補：同じ 記録の仕方の、ほかの種目（最初から用意した種目も 選べる）
  const targetsFor = (e) => catalog.exercises.filter((c) => c.id !== e.id && c.kind === e.kind)
    .map((c) => `<option value="${c.id}">${esc(c.name)}（${esc(partName(c.part))}）</option>`).join("");
  const ownedRows = owned.map((e) => `
    <div class="member-row" data-exercise="${e.id}">
      <div><strong>${esc(e.name)}</strong> <span class="hint">${esc(partName(e.part))}・${esc(state.kinds?.[e.kind] ?? "")}・記録 ${e.sets}件</span></div>
      <div class="row wrap">
        <button type="button" class="small" data-rename>名前を直す</button>
        <select class="merge-into"><option value="">まとめ先を選ぶ</option>${targetsFor(e)}</select>
        <button type="button" class="small" data-merge>まとめる</button>
      </div>
    </div>`).join("");
  // 1人ずつ：押すと 操作のボタン（トレーナーにする・ポジション・使えなくする・仮のパスワード）が 出てくる
  const userRow = (u) => `
    <details class="member-row${u.active ? "" : " off"}" data-user="${u.id}"${openMembers.has(u.id) && !u.me ? " open" : ""}>
      <summary class="member-summary">${avatar(u)}
        <div class="grow"><strong>${esc(u.name)}</strong>${positionTag(u)}
          ${u.role === "trainer" ? '<span class="role">トレーナー</span>' : ""}${u.active ? "" : '<span class="hint">（使えなくした）</span>'}
          <div class="hint">${esc(u.login_id)}・${u.last_day ? `最後：${formatDay(u.last_day)}・この7日 ${u.week_count}回` : "まだ記録なし"}</div></div>
        ${u.me ? '<span class="hint">自分</span>' : '<span class="member-more">操作</span>'}
      </summary>
      ${u.me ? "" : `<div class="member-actions row wrap">
        ${u.active ? `<button type="button" class="small" data-role="${u.role === "trainer" ? "member" : "trainer"}">
          ${u.role === "trainer" ? "選手にする" : "トレーナーにする"}</button>` : ""}
        ${team.positions.length && u.role === "member" && u.active ? `<select class="position-select" data-position aria-label="ポジション">
          <option value="">ポジション未設定</option>${team.positions.map((p) => `<option${p === u.position ? " selected" : ""}>${esc(p)}</option>`).join("")}
        </select>` : ""}
        <button type="button" class="small" data-active="${u.active ? 0 : 1}">${u.active ? "使えなくする" : "使えるようにする"}</button>
        ${u.active ? '<button type="button" class="small" data-reset>仮のパスワード</button>' : ""}
      </div>`}
    </details>`;
  // ポジションごとに 並べて、ポジションごとに たたむ（使えなくした人は 最後に まとめる）
  const inactive = team.users.filter((u) => !u.active);
  const memberGroups = [...groupByPosition(team.users.filter((u) => u.active)), ...(inactive.length ? [{ label: "使えなくした人", members: inactive }] : [])];
  const users = memberGroups.map((g) => `
    <details class="pos-fold" data-group="${esc(g.label)}"${openPositions.has(g.label) ? " open" : ""}>
      <summary class="pos-fold-head"><strong>${esc(g.label)}</strong><span class="hint">${g.members.length}人</span></summary>
      ${g.members.map(userRow).join("")}
    </details>`).join("");

  body.innerHTML = `
    <div class="card">
      <strong>招待の合言葉</strong>
      <p class="invite">${esc(team.invite_code)}</p>
      <p class="hint">新しい人は、最初の画面の「チームに入る」で この合言葉を入れます</p>
      <button type="button" id="new-invite" class="small">合言葉を作り直す</button>
    </div>
    <div class="card">
      <strong>人数</strong>
      <p>選手 <strong>${team.members.count}</strong> / ${team.members.max}人　トレーナー <strong>${team.trainers.count}</strong> / ${team.trainers.max}人</p>
    </div>
    <div class="card">
      <strong>競技</strong>
      <p class="hint">記録の画面の 最後のボタンに、競技向けの種目が出ます</p>
      <div class="row"><select id="team-sport" class="grow">${Object.entries(team.sports).map(([key, label]) =>
        `<option value="${key}"${key === team.sport ? " selected" : ""}>${esc(label)}</option>`).join("")}</select>
        <button type="button" id="save-sport" class="small dark">変える</button></div>
      ${team.sport ? "" : '<p class="hint alert-text">まだ 競技が決まっていません。選んで「変える」を押してね</p>'}
    </div>
    <details class="card fold-card" id="members-fold"${membersOpen ? " open" : ""}>
      <summary><strong>メンバー</strong><span class="hint">${team.users.filter((u) => u.active).length}人</span></summary>
      ${users}
    </details>
    <div class="card">
      <strong>チームで足した種目</strong>
      <p class="hint">名前を直したり、同じ種目を まとめたりできます（例：「ベンチ」を「ベンチプレス」に）。まとめると、今までの記録・グラフ・自己ベストが 1本になります。<strong>元には戻せません</strong></p>
      ${ownedRows || '<p class="hint">まだ ありません（記録の画面の「＋種目」で足せます）</p>'}
    </div>
    <div class="card">
      <strong>よく使う言葉</strong>
      <p class="hint">受信箱で ボタン1つで コメントに入れられます（自分用）</p>
      <div class="chips">${replies.map((r) =>
        `<span class="chip">${esc(r.body)} <button type="button" class="link" data-del-reply="${r.id}" aria-label="消す">×</button></span>`).join("")}</div>
      <form id="reply-form" class="row"><input name="body" class="grow" maxlength="60" placeholder="例：ナイス！"><button class="small">足す</button></form>
    </div>`;

  const reload = () => renderTeam(body);
  $("#members-fold", body).addEventListener("toggle", (event) => (membersOpen = event.currentTarget.open));
  // 開いた ポジション・人を 覚えておく（操作のあと 描き直しても そのまま）。見出しを 押したときだけ 覚える
  $("#members-fold", body).addEventListener("click", (event) => {
    const summary = event.target.closest("summary");
    if (!summary || summary.parentElement.id === "members-fold") return;
    const fold = summary.parentElement;
    const [set, key] = fold.dataset.group !== undefined ? [openPositions, fold.dataset.group] : [openMembers, Number(fold.dataset.user)];
    if (fold.open) set.delete(key);
    else set.add(key);
  });
  $("#save-sport", body).addEventListener("click", (event) => run(event.currentTarget, async () => {
    await api("/admin/team", { method: "PATCH", body: { sport: $("#team-sport", body).value } });
    toast("競技を変えました。画面を 読み込み直すと 記録の画面に出ます");
    location.reload();
  }));
  body.querySelectorAll("[data-exercise]").forEach((row) => {
    const id = row.dataset.exercise;
    const name = row.querySelector("strong").textContent;
    $("[data-rename]", row).addEventListener("click", (event) => {
      const next = prompt("新しい名前", name);
      if (!next?.trim() || next.trim() === name) return;
      run(event.currentTarget, async () => {
        await api(`/admin/exercises/${id}`, { method: "PATCH", body: { name: next.trim() } });
        await reload();
      });
    });
    $("[data-merge]", row).addEventListener("click", (event) => {
      const select = $(".merge-into", row);
      if (!select.value) return toast("まとめ先を 選んでね");
      const into = select.selectedOptions[0].textContent;
      if (!confirm(`「${name}」を「${into}」に まとめますか？

今までの記録は ぜんぶ「${into}」になり、「${name}」は 消えます。元には戻せません。`)) return;
      run(event.currentTarget, async () => {
        const result = await api(`/admin/exercises/${id}/merge`, { method: "POST", body: { into_id: Number(select.value) } });
        toast(`まとめました（記録 ${result.moved_sets}件）`);
        await reload();
      });
    });
  });
  $("#new-invite", body).addEventListener("click", (event) => {
    if (!confirm("合言葉を作り直しますか？（前の合言葉は 使えなくなります）")) return;
    run(event.currentTarget, async () => {
      await api("/admin/invite", { method: "POST" });
      await reload();
    });
  });
  body.querySelectorAll("[data-user]").forEach((row) => {
    const id = row.dataset.user;
    const patch = (button, payload, after) => run(button, async () => {
      const result = await api(`/admin/members/${id}`, { method: "PATCH", body: payload });
      after?.(result);
      await reload();
    });
    $("[data-role]", row)?.addEventListener("click", (event) => patch(event.currentTarget, { role: event.currentTarget.dataset.role }));
    $("[data-position]", row)?.addEventListener("change", (event) => patch(event.currentTarget, { position: event.currentTarget.value }));
    $("[data-active]", row)?.addEventListener("click", (event) => {
      const active = event.currentTarget.dataset.active === "1";
      if (!active && !confirm("この人を 使えなくしますか？（記録は残ります。ログインだけ できなくなります）")) return;
      patch(event.currentTarget, { active });
    });
    $("[data-reset]", row)?.addEventListener("click", (event) => {
      if (!confirm("仮のパスワードを出しますか？（今のパスワードは 使えなくなります）")) return;
      patch(event.currentTarget, { reset_password: true }, (result) =>
        alert(`仮のパスワード：${result.temporary_password}\n\n本人に伝えてください。この画面を閉じると もう見られません。`));
    });
  });
  body.querySelectorAll("[data-del-reply]").forEach((button) => button.addEventListener("click", () => run(button, async () => {
    await api(`/admin/quick-replies/${button.dataset.delReply}`, { method: "DELETE" });
    await reload();
  })));
  $("#reply-form", body).addEventListener("submit", (event) => {
    event.preventDefault();
    const text = event.target.elements.body.value.trim();
    if (!text) return;
    run(event.target.querySelector("button"), async () => {
      await api("/admin/quick-replies", { method: "POST", body: { body: text } });
      await reload();
    });
  });
}
