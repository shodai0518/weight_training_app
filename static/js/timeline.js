// タイムラインと 投稿のカード（管理画面の受信箱でも 同じ部品を使う）
// カードには 1枚目の写真・まとめ・スタンプ・コメントの数だけを出す。💬 を押すと 投稿のくわしい画面が開く

import {
  $, api, avatar, closeSheet, esc, positionTag, formatDay, formatTime, icon, menuDetailHtml, openSheet, openWho, reactionPeople, run, shrinkPhoto, state, toast,
} from "./api.js";

const TREND = { up: "↑", down: "↓", same: "→" };

// 名前。押すと その人のページ（履歴・伸びのグラフ）が開く
export function userHtml(user) {
  const badge = user.role === "trainer" ? '<span class="role">トレーナー</span>' : "";
  return `${avatar(user)}<button type="button" class="name" data-act="member" data-id="${user.id}">${esc(user.name)}</button>${positionTag(user)}${badge}`;
}

// 筋トレのまとめ（種目ごとに セット数と いちばん強いセット。押すと 全部のセットが開く）
export function summaryHtml(exercises, { trend = false, open = false } = {}) {
  if (!exercises.length) return "";
  const rows = exercises.map((e) => {
    const pr = e.has_pr ? '<span class="pr">★自己ベスト</span>' : "";
    const diff = trend && e.trend
      ? `<span class="trend ${e.trend.dir}">${TREND[e.trend.dir]} 前回 ${esc(e.trend.previous)}</span>`
      : trend ? '<span class="trend">初めて</span>' : "";
    const note = e.note ? `<span class="ex-note">${icon("note")}${esc(e.note)}</span>` : "";
    return `<li><span class="ex-name">${esc(e.name)}</span><span class="ex-count">${e.count}セット</span>` +
      `<span class="ex-best">${esc(e.best)}</span>${pr}${diff}${note}</li>`;
  }).join("");
  const all = exercises.map((e) =>
    `<div class="set-group"><strong>${esc(e.name)}</strong><ol>${e.sets.map((s) =>
      `<li>${esc(s.label)}${s.is_pr ? ' <span class="pr">★</span>' : ""}</li>`).join("")}</ol></div>`).join("");
  return `<div class="summary"><ul class="ex-list">${rows}</ul>` +
    `<details${open ? " open" : ""}><summary>全部のセットを見る</summary>${all}</details></div>`;
}

// スタンプの列。commentCount を渡すと、同じ列に 💬（コメントの数）のボタンも出す
function reactionsHtml(list, kind, id, commentCount = null) {
  const chips = list.map((r) =>
    `<button type="button" class="chip${r.mine ? " mine" : ""}" data-act="react" data-kind="${kind}" data-id="${id}" ` +
    `data-emoji="${r.emoji}" data-mine="${r.mine ? 1 : 0}" data-names="${esc(r.names.join("\n"))}" title="長押しで だれが押したか">${r.emoji} ${r.count}</button>`).join("");
  const picker = state.reactions.map((emoji) =>
    `<button type="button" class="pick" data-act="react" data-kind="${kind}" data-id="${id}" data-emoji="${emoji}" data-mine="0">${emoji}</button>`).join("");
  const who = list.length ? '<button type="button" class="link" data-act="who">だれが押したか見る</button>' : "";
  const comments = commentCount === null ? ""
    : `<button type="button" class="chip comment-count" data-act="open" aria-label="コメントを見る">${icon("comment")}${commentCount}</button>`;
  return `<div class="reactions">${chips}<button type="button" class="chip add" data-act="picker">スタンプ</button>${comments}` +
    `<div class="picker" hidden><div class="picks">${picker}</div><div class="who">${who}</div></div></div>`;
}

function commentHtml(c, isReply = false) {
  return `
    <div class="comment${c.user.role === "trainer" ? " trainer" : ""}${isReply ? " reply" : ""}">
      <div class="comment-head">${userHtml(c.user)}<time>${formatTime(c.created_at)}</time>
        <button type="button" class="link" data-act="reply" data-id="${c.id}" data-name="${esc(c.user.name)}">返信</button>
        ${c.can_delete ? `<button type="button" class="link" data-act="del-comment" data-id="${c.id}">消す</button>` : ""}</div>
      <p>${esc(c.body)}</p>
      ${reactionsHtml(c.reactions, "comments", c.id)}
      ${(c.replies ?? []).map((r) => commentHtml(r, true)).join("")}
    </div>`;
}

function commentsHtml(comments) {
  return comments.map((c) => commentHtml(c)).join("");
}

// 「○○さんに返信」の表示（コメントを書くところの上に出す）
const replyingHtml = '<div class="replying" hidden><span></span><button type="button" class="link" data-act="reply-cancel">やめる</button></div>';

export function postHead(post, { editable = false } = {}) {
  const minutes = post.workout.minutes ? `・${post.workout.minutes}分` : "";
  const when = post.workout.backfill && post.workout.day ? `<span class="tag">${formatDay(post.workout.day)}の分</span>` : "";
  return `<header class="post-head">${userHtml(post.user)}${when}
    <time>${formatTime(post.created_at)}${minutes}</time>
    ${editable && post.mine ? `<button type="button" class="link" data-act="edit-post">直す</button>` : ""}
    ${post.can_delete ? `<button type="button" class="link" data-act="del-post">消す</button>` : ""}</header>`;
}

// 写真。compact：1枚目だけを 横長で出す（残りは くわしい画面で）
function photosHtml(post, compact) {
  const expired = post.photos_expired ? '<p class="hint">写真は 保存期間をすぎたので 消えました</p>' : "";
  if (!post.photos.length) return expired;
  if (compact) {
    const rest = post.photos.length - 1;
    return `<div class="photos cover"><img src="${post.photos[0].url}" alt="写真" loading="lazy" data-act="${rest ? "open" : "photo"}">` +
      `${rest ? `<span class="more-photos">＋${rest}枚</span>` : ""}</div>${expired}`;
  }
  return `<div class="photos n${post.photos.length}">${post.photos.map((p) =>
    `<img src="${p.url}" alt="写真" loading="lazy" data-act="photo">`).join("")}</div>${expired}`;
}

export function postBody(post, { compact = false, ...options } = {}) {
  const menu = post.workout.menu_title ? `<p class="menu-tag">${icon("menu")}メニュー：${esc(post.workout.menu_title)}</p>` : "";
  return `${post.body ? `<p class="body">${esc(post.body)}</p>` : ""}${photosHtml(post, compact)}${menu}${summaryHtml(post.workout.exercises, options)}`;
}

// タイムラインのカード
export function postCard(post) {
  return `<article class="card post" data-post="${post.id}">
    ${postHead(post)}
    ${postBody(post, { compact: true })}
    ${reactionsHtml(post.reactions, "posts", post.id, post.comment_count)}
  </article>`;
}

// 投稿のくわしい画面（全部の写真・全部のセット・コメントの一覧・コメントを書くバー）
function postDetail(post) {
  return `<article class="post detail" data-post="${post.id}">
    ${postHead(post, { editable: true })}
    ${postBody(post, { open: true })}
    ${reactionsHtml(post.reactions, "posts", post.id)}
    <strong class="comments-title">コメント ${post.comment_count}件</strong>
    <div class="comments">${commentsHtml(post.comments) || '<p class="hint">まだコメントは ありません</p>'}</div>
    <form class="comment-form bar">${stampBarHtml(post)}${replyingHtml}<input name="body" maxlength="${state.limits.comment}" placeholder="コメントを書く…" autocomplete="off">
      <button type="submit" class="primary small">送る</button></form>
  </article>`;
}

// 受信箱のカードでも使う部品
// コメントを書く欄の すぐ上に いつも出す スタンプの列（スクロールしても 動かない。1回押すだけで 付く／外れる）
function stampBarHtml(post) {
  const mine = post.reactions.find((r) => r.mine)?.emoji;
  return `<div class="review-stamps">${state.reactions.map((emoji) => `<button type="button" class="pick${emoji === mine ? " mine" : ""}"
    data-act="react" data-kind="posts" data-id="${post.id}" data-emoji="${emoji}" data-mine="${emoji === mine ? 1 : 0}"
    aria-label="スタンプ ${emoji}">${emoji}</button>`).join("")}</div>`;
}

export const parts = { reactionsHtml, commentsHtml, replyingHtml, stampBarHtml };

// くわしい画面を 下から出す。changed(post)：中で変わったとき。removed()：投稿を消したとき
function openDetail(postId, { changed, removed }) {
  const box = openSheet("投稿", '<div class="detail-box"><p class="hint center">読み込み中…</p></div>').firstElementChild;
  let post = null;
  const show = (fresh) => {
    post = fresh;
    box.innerHTML = postDetail(post);
  };
  bindPosts(box, {
    refresh: (card, fresh) => {
      show(fresh);
      changed(fresh);
    },
    removed: () => {
      closeSheet();
      removed();
    },
  });
  bindEditor(box, () => post, (fresh) => {
    post = fresh;
    changed(fresh);
  }, () => show(post));
  api(`/posts/${postId}`).then(show).catch((error) => {
    box.innerHTML = `<p class="hint center">${esc(error.message)}</p>`;
  });
}

// ---- 投稿を直す（投稿したあとで ひとことを書き直す・写真を足す／外す）----

function editorHtml(post, draft) {
  const left = state.limits.photos - post.photos.length;
  return `<div class="post-editor" data-editor="${post.id}">
    <strong>投稿を直す</strong>
    <textarea class="edit-body" rows="4" maxlength="${state.limits.body}" placeholder="全体の ひとこと">${esc(draft)}</textarea>
    <div class="thumbs">${post.photos.map((p) =>
      `<span class="thumb"><img src="${p.url}" alt=""><button type="button" data-edit-act="photo-del" data-photo="${p.id}" aria-label="この写真を外す">×</button></span>`).join("")}</div>
    ${left > 0 ? `<label class="button small file">${icon("camera")}写真を足す（あと ${left}枚）
      <input class="edit-photo" type="file" accept="image/*" multiple hidden></label>` : `<p class="hint">写真は ${state.limits.photos}枚まで</p>`}
    <p class="hint">記録の数字と 種目ごとのメモは、投稿する前の画面で 直せます</p>
    <div class="row">
      <button type="button" class="small grow" data-edit-act="cancel">やめる</button>
      <button type="button" class="primary grow" data-edit-act="save">保存</button>
    </div>
  </div>`;
}

// getPost：今の投稿。saved(post)：直したあと（タイムラインのカードも 描き直す）。close()：くわしい画面に もどる
function bindEditor(box, getPost, saved, close) {
  const draw = (post, draft) => (box.innerHTML = editorHtml(post, draft));
  const draft = () => $(".edit-body", box)?.value ?? getPost().body;
  box.addEventListener("click", (event) => {
    const start = event.target.closest('[data-act="edit-post"]');
    if (start) return draw(getPost(), getPost().body);
    const button = event.target.closest("[data-edit-act]");
    if (!button) return;
    const act = button.dataset.editAct;
    const post = getPost();
    if (act === "cancel") return close();
    if (act === "photo-del") {
      if (!confirm("この写真を外しますか？")) return;
      const text = draft();
      return run(button, async () => {
        const fresh = await api(`/posts/${post.id}/photos/${button.dataset.photo}`, { method: "DELETE" });
        saved(fresh);
        draw(fresh, text);
      });
    }
    if (act === "save") {
      return run(button, async () => {
        saved(await api(`/posts/${post.id}`, { method: "PATCH", body: { body: draft() } }));
        toast("直しました");
        close();
      });
    }
  });
  box.addEventListener("change", async (event) => {
    if (!event.target.matches(".edit-photo")) return;
    const files = [...event.target.files];
    const text = draft();
    const post = getPost();
    const photos = [];
    for (const file of files.slice(0, state.limits.photos - post.photos.length)) {
      try {
        photos.push(await shrinkPhoto(file));
      } catch (error) {
        toast(error.message);
      }
    }
    if (!photos.length) return;
    run(null, async () => {
      const fresh = await api(`/posts/${post.id}/photos`, { method: "POST", body: { photos } });
      saved(fresh);
      draw(fresh, text);
    });
  });
}

// 投稿の中のボタン（スタンプ・コメント・消す・写真）を動かす。
// refresh(card, post)：サーバーから返ってきた 新しい投稿で 描き直す。removed(card)：消したあと
export function bindPosts(root, { refresh, removed }) {
  // スタンプを 長押し（パソコンは 右クリック）すると、だれが押したかの一覧（Slack と同じ）
  let pressTimer = null;
  let longPressed = false;
  const peopleOf = (row) => [...row.querySelectorAll(".chip[data-names]")].flatMap((chip) =>
    chip.dataset.names.split("\n").filter(Boolean).map((name) => ({ name, emoji: chip.dataset.emoji })));
  const stampChip = (event) => event.target.closest('.chip[data-act="react"]');
  root.addEventListener("pointerdown", (event) => {
    const chip = stampChip(event);
    if (!chip) return;
    longPressed = false;
    clearTimeout(pressTimer);
    pressTimer = setTimeout(() => {
      longPressed = true;
      openWho(peopleOf(chip.closest(".reactions")));
    }, 450);
  });
  ["pointerup", "pointerleave", "pointercancel"].forEach((type) => root.addEventListener(type, () => clearTimeout(pressTimer)));
  root.addEventListener("contextmenu", (event) => {
    const chip = stampChip(event);
    if (!chip) return;
    event.preventDefault();
    clearTimeout(pressTimer);
    longPressed = true;
    openWho(peopleOf(chip.closest(".reactions")));
  });

  root.addEventListener("click", (event) => {
    const target = event.target.closest("[data-act]");
    if (!target || !root.contains(target)) return;
    if (longPressed && stampChip(event)) { // 長押しのあとの「押した」は、スタンプを付け外ししない
      longPressed = false;
      return;
    }
    if (target.dataset.act === "who") return openWho(peopleOf(target.closest(".reactions")));
    const card = target.closest("[data-post]");
    const act = target.dataset.act;
    if (act === "member") {
      closeSheet();
      return state.go("member", Number(target.dataset.id));
    }
    if (act === "photo") {
      $("#viewer img").src = target.src;
      $("#viewer").hidden = false;
      return;
    }
    if (act === "picker") {
      const picker = target.parentElement.querySelector(".picker");
      picker.hidden = !picker.hidden;
      return;
    }
    if (!card) return;
    if (act === "reply" || act === "reply-cancel") {
      const zone = card.querySelector(".replying");
      if (!zone) return;
      if (act === "reply") {
        card.dataset.replyTo = target.dataset.id;
        zone.querySelector("span").textContent = `${target.dataset.name} さんに返信`;
      } else {
        delete card.dataset.replyTo;
      }
      zone.hidden = act !== "reply";
      if (act === "reply") card.querySelector(".comment-form input, textarea.reply")?.focus();
      return;
    }
    const postId = card.dataset.post;
    const current = () => root.querySelector(`[data-post="${postId}"]`); // 描き直されたあとの 今のカード
    if (act === "open") {
      return openDetail(postId, {
        changed: (post) => current() && refresh(current(), post),
        removed: () => current() && removed(current()),
      });
    }
    if (act === "react") {
      const { kind, id, emoji, mine } = target.dataset;
      return run(target, async () => {
        const post = mine === "1"
          ? await api(`/${kind}/${id}/reaction`, { method: "DELETE" })
          : await api(`/${kind}/${id}/reaction`, { method: "PUT", body: { emoji } });
        refresh(card, post);
      });
    }
    if (act === "del-comment") {
      if (!confirm("このコメントを消しますか？")) return;
      return run(target, async () => refresh(card, await api(`/comments/${target.dataset.id}`, { method: "DELETE" })));
    }
    if (act === "del-post") {
      if (!confirm("この投稿を消しますか？（筋トレの記録は 残ります）")) return;
      return run(target, async () => {
        await api(`/posts/${postId}`, { method: "DELETE" });
        removed(card);
        toast("投稿を消しました");
      });
    }
  });
  root.addEventListener("submit", (event) => {
    const form = event.target.closest(".comment-form");
    if (!form) return;
    event.preventDefault();
    const card = form.closest("[data-post]");
    const body = form.elements.body.value.trim();
    if (!body) return;
    run(form.querySelector("button"), async () => {
      const parent_id = card.dataset.replyTo ? Number(card.dataset.replyTo) : null;
      refresh(card, await api(`/posts/${card.dataset.post}/comments`, { method: "POST", body: { body, parent_id } }));
    });
  });
}

function replaceCard(card, post) {
  card.insertAdjacentHTML("afterend", postCard(post));
  card.remove();
}

export async function renderTimeline(view, { userId = null } = {}) {
  view.innerHTML = `
    <div id="menu-banners"></div>
    <div class="filter">
      <button type="button" data-filter="all" class="${userId ? "" : "on"}">みんな</button>
      <button type="button" data-filter="mine" class="${userId ? "on" : ""}">自分だけ</button>
    </div>
    <div id="posts"></div>
    <button type="button" id="more" class="wide" hidden>もっと見る</button>`;
  const list = $("#posts", view);
  const more = $("#more", view);
  let last = null;

  async function load() {
    const query = new URLSearchParams();
    if (last) query.set("before", last);
    if (userId) query.set("user_id", userId);
    const data = await api(`/timeline?${query}`);
    if (!data.posts.length && !last) {
      list.innerHTML = '<p class="hint center">まだ投稿がありません。<br>「記録」から 筋トレを始めよう！</p>';
    }
    list.insertAdjacentHTML("beforeend", data.posts.map(postCard).join(""));
    if (data.posts.length) last = data.posts[data.posts.length - 1].id;
    more.hidden = !data.more;
  }

  bindPosts(list, { refresh: replaceCard, removed: (card) => card.remove() });
  if (!userId) showMenuBanners($("#menu-banners", view));
  more.addEventListener("click", () => run(more, load));
  view.querySelectorAll("[data-filter]").forEach((button) =>
    button.addEventListener("click", () => state.go("timeline", button.dataset.filter === "mine" ? state.me.id : null)));
  await load();
}

// 配られているメニューを タイムラインの一番上に出す（押すと 中身が開く）
async function showMenuBanners(box) {
  let menus;
  try {
    menus = (await api("/menus")).filter((menu) => menu.status !== "over"); // 期間がすぎたものは「記録」の「過去のメニュー」から
  } catch {
    return; // 出せなくても タイムラインは 見られるようにする
  }
  const period = (menu) => (menu.start_day || menu.end_day
    ? `${menu.start_day ? formatDay(menu.start_day) : ""}〜${menu.end_day ? formatDay(menu.end_day) : ""}` : "");
  const mark = { now: "", soon: "これから" };
  box.innerHTML = menus.map((menu) => `
    <button type="button" class="menu-banner" data-menu-open="${menu.id}">
      <span class="grow"><strong>${icon("menu")}${esc(menu.title)}</strong>
        <span class="hint">${[period(menu), mark[menu.status]].filter(Boolean).join("・") || "トレーナーからのメニュー"}</span></span>
      <span class="menu-banner-open">見る</span>
    </button>`).join("");
  box.querySelectorAll("[data-menu-open]").forEach((row) => row.addEventListener("click", () => {
    const menu = menus.find((m) => m.id === Number(row.dataset.menuOpen));
    const sheet = openSheet(menu.title, `${menuDetailHtml(menu)}
      <button type="button" class="dark wide" id="menu-go">「記録」の画面で このメニューを始める</button>`);
    $("#menu-go", sheet).addEventListener("click", () => {
      closeSheet();
      state.go("workout");
    });
  }));
}

// 1つの投稿だけを くわしく出す（知らせから開いたとき）
export async function renderPost(view, postId) {
  view.innerHTML = '<button type="button" class="link back">← タイムラインへ</button><div id="posts" class="card"></div>';
  $(".back", view).addEventListener("click", () => state.go("timeline"));
  const list = $("#posts", view);
  bindPosts(list, { refresh: (card, post) => (list.innerHTML = postDetail(post)), removed: () => state.go("timeline") });
  list.innerHTML = postDetail(await api(`/posts/${postId}`));
}
