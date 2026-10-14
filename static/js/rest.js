// セットの間の休憩タイマー（kintore_app と同じ作り ＋ 休憩おわりの通知）
// 残り時間は「終わる時刻」から計算するので、別のアプリから戻ってきても 正しい時間が出る

import { $, api, pad, run, toast } from "./api.js";
import * as notify from "./notify.js";

const REST_KEY = "rest_timer";
const PUSH_CANCEL_SECONDS = 3; // 画面を見ているなら、終わる少し前に サーバーの通知を取り消す（音と帯で わかるので）

const restBar = $("#rest");
const restTime = $("#rest-time");
const restName = $("#rest-name");
const restSound = $("#rest-sound");
const restNotify = $("#rest-notify");

let rest = null; // { exerciseId, name, seconds（決めた長さ）, endAt（終わる時刻）, rung（もう知らせたか）, pushed（サーバーに予約したか）, limits }
let ticker = null;
let wakeLock = null;

const formatRest = (seconds) => `${Math.floor(seconds / 60)}:${pad(seconds % 60)}`;
const save = () => notify.store.set(REST_KEY, rest ? JSON.stringify(rest) : null);

// タイマーが動いている間は 画面が暗くならないようにする
async function keepAwake(on) {
  try {
    if (on && !wakeLock && "wakeLock" in navigator && document.visibilityState === "visible") {
      wakeLock = await navigator.wakeLock.request("screen");
      wakeLock.addEventListener("release", () => (wakeLock = null));
    } else if (!on && wakeLock) {
      await wakeLock.release();
      wakeLock = null;
    }
  } catch {
    wakeLock = null;
  }
}

// サーバーに「この時刻に 休憩おわりを送って」と頼む（画面を消していても スマホに出る）
async function schedulePush() {
  if (!rest || !(await notify.pushEnabled())) return;
  const seconds = (rest.endAt - Date.now()) / 1000;
  if (seconds <= PUSH_CANCEL_SECONDS) return;
  rest.pushed = true;
  save();
  api("/rest/push", { method: "POST", body: { seconds, name: rest.name } }).catch(() => {});
}

function cancelPush() {
  if (!rest?.pushed) return;
  rest.pushed = false;
  save();
  api("/rest/push", { method: "DELETE" }).catch(() => {});
}

function draw() {
  if (!rest) return;
  const left = Math.ceil((rest.endAt - Date.now()) / 1000);
  const over = left <= 0;
  restTime.textContent = over ? `+${formatRest(-left)}` : formatRest(left);
  restBar.classList.toggle("over", over);
  // 画面を見ているときは 音と帯で知らせるので、サーバーの通知は 出さない
  if (left <= PUSH_CANCEL_SECONDS && document.visibilityState === "visible") cancelPush();
  if (over && !rest.rung) {
    rest.rung = true;
    save();
    notify.beep();
    notify.vibrate();
    // 画面がかくれているのに ここが動いたとき（パソコンの別のタブ など）は、画面の中から通知を出す
    if (document.visibilityState !== "visible" && !rest.pushed) {
      notify.showNotification("休憩おわり！", `${rest.name}：次のセットへ`, "rest");
    }
    restBar.classList.add("flash");
    setTimeout(() => restBar.classList.remove("flash"), 2400);
  }
  if (-left > 600) stopRest(); // 10分すぎても そのままなら 片づける
}

async function show() {
  restBar.hidden = false;
  document.body.classList.add("resting");
  restName.textContent = `（${rest.name}・${formatRest(rest.seconds)}）`;
  restSound.textContent = notify.soundOn() ? "音 オン" : "音 オフ";
  clearInterval(ticker);
  ticker = setInterval(draw, 250);
  draw();
  keepAwake(true);
  restNotify.hidden = notify.pushSupport() !== "ok" || (await notify.pushEnabled());
}

export function startRest(exercise, limits) {
  if (!exercise?.rest_seconds) return;
  notify.prepareAudio();
  rest = {
    exerciseId: exercise.id,
    name: exercise.name,
    seconds: exercise.rest_seconds,
    endAt: Date.now() + exercise.rest_seconds * 1000,
    rung: false,
    pushed: false,
    limits,
  };
  save();
  show();
  schedulePush();
}

export function stopRest() {
  if (!rest) return;
  cancelPush();
  rest = null;
  save();
  clearInterval(ticker);
  restBar.hidden = true;
  restBar.classList.remove("over", "flash");
  document.body.classList.remove("resting");
  keepAwake(false);
}

// 長さを変える：今のタイマーに足し引きして、この種目の 自分の長さとして覚える
function adjust(delta) {
  if (!rest) return;
  notify.prepareAudio();
  const { min = 15, max = 600 } = rest.limits ?? {};
  const seconds = Math.min(max, Math.max(min, rest.seconds + delta));
  const change = seconds - rest.seconds;
  if (!change) return;
  rest.seconds = seconds;
  rest.endAt += change * 1000;
  if (rest.endAt > Date.now()) rest.rung = false; // のばして まだ時間が残っていれば、また知らせる
  save();
  show();
  schedulePush();
  api(`/exercises/${rest.exerciseId}/rest`, { method: "PUT", body: { seconds } }).catch(() => {});
}

$("#rest-minus").addEventListener("click", () => adjust(-(rest?.limits?.step ?? 15)));
$("#rest-plus").addEventListener("click", () => adjust(rest?.limits?.step ?? 15));
$("#rest-skip").addEventListener("click", stopRest);
restSound.addEventListener("click", () => {
  notify.setSound(!notify.soundOn());
  restSound.textContent = notify.soundOn() ? "音 オン" : "音 オフ";
  if (notify.soundOn()) {
    notify.prepareAudio();
    notify.beep(); // どんな音か 聞かせる
  }
});
restNotify.addEventListener("click", () =>
  run(restNotify, async () => {
    await notify.enablePush();
    restNotify.hidden = true;
    toast("通知をオンにしました");
    schedulePush();
  }),
);
document.addEventListener("visibilitychange", () => {
  if (!rest) return;
  if (document.visibilityState === "visible") {
    keepAwake(true);
    draw();
  } else if (!rest.pushed && !rest.rung) {
    schedulePush(); // 終わる直前に取り消したあとで 画面をかくしたら、もう一度 頼む
  }
});

// 画面を開き直したときに、動いていたタイマーを つづける
export function restoreRest() {
  try {
    const saved = JSON.parse(notify.store.get(REST_KEY) || "null");
    if (saved && Date.now() - saved.endAt < 600000) {
      rest = saved;
      show();
    } else {
      notify.store.set(REST_KEY, null);
    }
  } catch {
    notify.store.set(REST_KEY, null);
  }
}
