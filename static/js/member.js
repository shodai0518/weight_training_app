// メンバーの一覧と、1人ずつのページ（筋トレの履歴・種目ごとの伸び）

import { $, api, avatar, esc, formatDay, groupByPosition, positionTag, run, state, toast } from "./api.js";
import { askPosition } from "./app.js";
import * as notify from "./notify.js";
import { summaryHtml } from "./timeline.js";
import { renderCompose } from "./workout.js";

let memberFilter = "all"; // メンバーの一覧で しぼっている ポジション

export async function renderMembers(view) {
  const members = await api("/members");
  const trainer = state.me.role === "trainer";
  const groups = groupByPosition(members);
  if (!groups.some((g) => g.label === memberFilter)) memberFilter = "all";
  const row = (m) => {
    const last = m.last_day ? `最後：${formatDay(m.last_day)}` : "まだ記録なし";
    const idle = trainer && m.role === "member" && m.idle ? '<span class="alert">7日以上 あいています</span>' : "";
    return `<button type="button" class="list-row" data-id="${m.id}">
      <span class="list-main">${avatar(m)}<strong>${esc(m.name)}</strong>${positionTag(m)}${m.role === "trainer" ? '<span class="role">トレーナー</span>' : ""}
        ${m.recent_pr ? '<span class="pr">★自己ベスト</span>' : ""}${idle}</span>
      <span class="hint">${last}・この7日 ${m.week_count}回</span>
    </button>`;
  };
  const shown = groups.filter((g) => memberFilter === "all" || g.label === memberFilter);
  view.innerHTML = `
    ${groups.length > 1 ? `<div class="chips position-filter">
      <button type="button" class="chip${memberFilter === "all" ? " on" : ""}" data-filter-position="all">全員 ${members.length}</button>
      ${groups.map((g) => `<button type="button" class="chip${memberFilter === g.label ? " on" : ""}" data-filter-position="${esc(g.label)}">${esc(g.label)} ${g.members.length}</button>`).join("")}
    </div>` : ""}
    ${shown.map((g) => `<p class="group-head">${esc(g.label)}<span>${g.members.length}人</span></p>
      <div class="card list">${g.members.map(row).join("")}</div>`).join("")}`;
  view.querySelectorAll("[data-filter-position]").forEach((chip) => chip.addEventListener("click", () => {
    memberFilter = chip.dataset.filterPosition;
    renderMembers(view);
  }));
  view.querySelectorAll("[data-id]").forEach((item) =>
    item.addEventListener("click", () => state.go("member", Number(item.dataset.id))));
}

// 折れ線グラフ（1つの種目の 見つもり1RM の伸び）
function chartHtml(points, caption) {
  if (points.length < 2) {
    return `<p class="hint">記録が 2回以上になると グラフが出ます${points.length ? `（今：${esc(points[0].label)}）` : ""}</p>`;
  }
  const W = 320, H = 150, left = 36, right = 12, top = 12, bottom = 24;
  const values = points.map((p) => p.value);
  let min = Math.min(...values), max = Math.max(...values);
  if (min === max) { min -= 1; max += 1; }
  const x = (i) => left + (i * (W - left - right)) / (points.length - 1);
  const y = (v) => top + ((max - v) * (H - top - bottom)) / (max - min);
  const line = points.map((p, i) => `${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join(" ");
  const lastPoint = points[points.length - 1];
  const dots = points.map((p, i) =>
    `<circle cx="${x(i).toFixed(1)}" cy="${y(p.value).toFixed(1)}" r="${i === points.length - 1 ? 4.5 : 2.5}" ` +
    `class="${i === points.length - 1 ? "last" : ""}"><title>${esc(formatDay(p.day))} ${esc(p.label)}</title></circle>`).join("");
  const unit = lastPoint.unit;
  const diff = Math.round((lastPoint.value - points[0].value) * 10) / 10;
  return `
    <svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="伸びのグラフ">
      <line class="axis" x1="${left}" y1="${top}" x2="${W - right}" y2="${top}"/>
      <line class="axis" x1="${left}" y1="${H - bottom}" x2="${W - right}" y2="${H - bottom}"/>
      <text x="${left - 4}" y="${top + 4}" text-anchor="end">${max}</text>
      <text x="${left - 4}" y="${H - bottom + 4}" text-anchor="end">${min}</text>
      <text x="${left}" y="${H - 6}">${esc(formatDay(points[0].day))}</text>
      <text x="${W - right}" y="${H - 6}" text-anchor="end">${esc(formatDay(lastPoint.day))}</text>
      <polyline points="${line}"/>${dots}
    </svg>
    <p class="hint">${esc(caption)}。
      今：<strong>${lastPoint.value}${unit}</strong>（${esc(lastPoint.label)}）／ 最初から ${diff >= 0 ? "+" : ""}${diff}${unit}</p>`;
}

// 体重・体脂肪率（自分とトレーナーだけに見える。入れられるのは 自分だけ）
const BODY_KINDS = {
  weight: { label: "体重", unit: "kg" },
  body_fat: { label: "体脂肪率", unit: "%" },
};

function bodyPoints(logs, kind) {
  const unit = BODY_KINDS[kind].unit;
  return logs.filter((log) => log[kind] != null).map((log) => ({ day: log.day, value: log[kind], unit, label: `${log[kind]}${unit}` }));
}

function bodyHtml(logs, mine) {
  const recent = [...logs].reverse().slice(0, 10).map((log) => `
    <li><span>${formatDay(log.day)}</span>
      <span>${log.weight != null ? `${log.weight}kg` : "-"}</span>
      <span>${log.body_fat != null ? `${log.body_fat}%` : "-"}</span>
      ${mine ? `<button type="button" class="link" data-del-body="${log.id}">消す</button>` : ""}</li>`).join("");
  return `
    <div class="card">
      <strong>体重・体脂肪率</strong>
      <p class="hint">見えるのは ${mine ? "自分" : "本人"}とトレーナーだけです</p>
      ${mine ? `<form id="body-form">
        <div class="inputs">
          <label>体重 <input name="weight" type="number" inputmode="decimal" min="20" max="300" step="0.1"> kg</label>
          <label>体脂肪率 <input name="body_fat" type="number" inputmode="decimal" min="1" max="70" step="0.1"> %</label>
        </div>
        <button type="submit" class="dark wide">今日の分を記録する</button>
        <p class="hint">片方だけでもOK。同じ日に入れ直すと 上書きします</p>
      </form>` : ""}
      ${logs.length ? `<div class="filter small-filter">${Object.entries(BODY_KINDS).map(([key, kind], index) =>
        `<button type="button" data-body-kind="${key}" class="${index === 0 ? "on" : ""}">${kind.label}</button>`).join("")}</div>
        <div id="body-chart"></div>
        <ul class="body-list">${recent}</ul>` : '<p class="hint">まだ記録がありません</p>'}
    </div>`;
}

async function settingsHtml() {
  const support = notify.pushSupport();
  const enabled = await notify.pushEnabled();
  const message = {
    ok: enabled ? "オンです。休憩が終わったとき・コメントが付いたときに 知らせます" : "オンにすると、画面を消していても 休憩の終わりを知らせます",
    insecure: "このアドレス（http）では 使えません。インターネット上に置く（https）と 使えます",
    "ios-needs-home": "iPhone は、Safari の「共有」→「ホーム画面に追加」で入れたアプリから開くと 使えます",
    unsupported: "このブラウザでは 使えません",
  }[support];
  return `
    <div class="card">
      <strong>通知</strong>
      <p class="hint">${message}</p>
      ${support === "ok" ? `<div class="row">
        <button type="button" id="push-toggle" class="small">${enabled ? "通知をオフにする" : "通知をオンにする"}</button>
        ${enabled ? '<button type="button" id="push-test" class="small">ためしに送る</button>' : ""}
      </div>` : ""}
    </div>
    <div class="card">
      <strong>アカウント</strong>
      <p class="hint">ログインID：${esc(state.me.login_id)}</p>
      <button type="button" id="logout" class="small">ログアウト</button>
    </div>`;
}

// ポジション：本人は「変える」ボタン、トレーナーは ほかの選手のページで その場で変えられる
function positionCardHtml(user, mine, trainer) {
  if (!state.positions.length || user.role !== "member") return "";
  const current = mine ? state.me.position : user.position;
  if (mine) {
    return `<div class="card position-card">
      <div class="row between"><div><strong>ポジション</strong><p class="hint">変わったら ここから 変えられます</p></div>
        <span class="pos big">${esc(current || "未設定")}</span></div>
      <button type="button" id="change-position" class="small dark">ポジションを変える</button>
    </div>`;
  }
  if (!trainer) return "";
  return `<div class="card position-card">
    <div class="row between"><div><strong>ポジション</strong><p class="hint">トレーナーが 変えられます</p></div>
      <select id="member-position" class="position-select" aria-label="ポジション">
        <option value="">未設定</option>${state.positions.map((p) => `<option${p === current ? " selected" : ""}>${esc(p)}</option>`).join("")}
      </select></div>
  </div>`;
}

export async function renderMember(view, memberId) {
  const mine = memberId === state.me.id;
  const trainer = state.me.role === "trainer"; // メンバーの一覧は トレーナーだけ。選手は タイムラインへ戻る
  const data = await api(`/members/${memberId}`);
  let selected = data.series[0]?.exercise_id ?? null;

  const history = data.history.map((w) => `
    <div class="history-item">
      <div class="row between"><strong>${formatDay(w.day)}</strong><span class="hint">${w.minutes ?? "-"}分</span></div>
      ${summaryHtml(w.exercises)}
      ${w.post_id ? `<button type="button" class="link" data-post="${w.post_id}">投稿を見る</button>`
        : mine ? `<button type="button" class="link" data-compose="${w.id}">タイムラインに投稿する</button>` : ""}
    </div>`).join("");

  view.innerHTML = `
    ${mine ? "" : `<button type="button" class="link back">← ${trainer ? "メンバーへ" : "タイムラインへ"}</button>`}
    <div class="card">
      <div class="row">${avatar(data.user)}<strong class="title">${esc(data.user.name)}</strong>${positionTag(data.user)}
        ${data.user.role === "trainer" ? '<span class="role">トレーナー</span>' : ""}
        ${data.user.active ? "" : '<span class="hint">（やめた人）</span>'}</div>
    </div>
    ${positionCardHtml(data.user, mine, trainer)}
    <div class="card">
      <strong>種目ごとの伸び</strong>
      ${data.series.length ? `<select id="series">${data.series.map((s) =>
        `<option value="${s.exercise_id}">${esc(s.name)}（${s.points.length}回）</option>`).join("")}</select>` : ""}
      <div id="chart"></div>
    </div>
    ${data.body ? bodyHtml(data.body, mine) : ""}
    <div class="card">
      <strong>筋トレの履歴</strong>
      ${history || '<p class="hint">まだ記録がありません</p>'}
    </div>
    <div id="settings"></div>`;

  const drawChart = () => {
    const series = data.series.find((s) => s.exercise_id === selected);
    const CAPTIONS = { kg: "1回だけ持ち上げられる重さの 見つもり（kg）", 回: "回数", 本: "本数", 秒: "時間（秒）", 分: "時間（分）", m: "合計の距離（m）" };
    const caption = CAPTIONS[series?.points[series.points.length - 1]?.unit] ?? "";
    $("#chart", view).innerHTML = series ? chartHtml(series.points, caption) : '<p class="hint">まだ記録がありません</p>';
  };
  drawChart();

  // 体重・体脂肪率
  const drawBody = (kind) => {
    const box = $("#body-chart", view);
    if (!box) return;
    view.querySelectorAll("[data-body-kind]").forEach((button) => button.classList.toggle("on", button.dataset.bodyKind === kind));
    box.innerHTML = chartHtml(bodyPoints(data.body, kind), BODY_KINDS[kind].label);
  };
  if (data.body) drawBody("weight");
  view.querySelectorAll("[data-body-kind]").forEach((button) => button.addEventListener("click", () => drawBody(button.dataset.bodyKind)));
  $("#body-form", view)?.addEventListener("submit", (event) => {
    event.preventDefault();
    const value = (name) => (event.target.elements[name].value === "" ? null : Number(event.target.elements[name].value));
    run(event.target.querySelector("button[type=submit]"), async () => {
      await api("/body", { method: "PUT", body: { weight: value("weight"), body_fat: value("body_fat") } });
      toast("記録しました");
      state.go("me");
    });
  });
  view.querySelectorAll("[data-del-body]").forEach((button) => button.addEventListener("click", () => {
    if (!confirm("この記録を消しますか？")) return;
    run(button, async () => {
      await api(`/body/${button.dataset.delBody}`, { method: "DELETE" });
      state.go("me");
    });
  }));
  $("#series", view)?.addEventListener("change", (event) => {
    selected = Number(event.target.value);
    drawChart();
  });
  $("#member-position", view)?.addEventListener("change", (event) => {
    const select = event.currentTarget; // 待っている間に event.currentTarget は 空になるので、先に とっておく
    run(select, async () => {
      await api(`/admin/members/${memberId}`, { method: "PATCH", body: { position: select.value } });
      toast(`${data.user.name} さんの ポジションを「${select.value || "未設定"}」にしました`);
    });
  });
  $("#change-position", view)?.addEventListener("click", () => askPosition({ done: () => state.go("me") }));
  $(".back", view)?.addEventListener("click", () => state.go(trainer ? "members" : "timeline"));
  view.querySelectorAll("[data-post]").forEach((button) =>
    button.addEventListener("click", () => state.go("post", Number(button.dataset.post))));
  view.querySelectorAll("[data-compose]").forEach((button) => button.addEventListener("click", () => {
    const workout = data.history.find((w) => w.id === Number(button.dataset.compose));
    renderCompose(view, workout);
  }));

  if (!mine) return;
  const drawSettings = async () => {
    $("#settings", view).innerHTML = await settingsHtml();
    $("#push-toggle", view)?.addEventListener("click", (event) => run(event.currentTarget, async () => {
      if (await notify.pushEnabled()) {
        await notify.disablePush();
        toast("通知をオフにしました");
      } else {
        await notify.enablePush();
        toast("通知をオンにしました");
      }
      await drawSettings();
    }));
    $("#push-test", view)?.addEventListener("click", (event) => run(event.currentTarget, async () => {
      await api("/push/test", { method: "POST" });
      toast("送りました。数秒で 届きます");
    }));
    $("#logout", view).addEventListener("click", (event) => run(event.currentTarget, async () => {
      await notify.disablePush().catch(() => {}); // このスマホに ほかの人の知らせが 届かないように
      await api("/logout", { method: "POST" });
      state.onLoggedOut();
    }));
  };
  await drawSettings();
}
