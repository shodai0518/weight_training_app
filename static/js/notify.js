// 知らせ方を 1か所にまとめる（音・ふるえる・通知）。
// ストアに出すアプリにするときは、このファイルを アプリ用の知らせに差しかえる。

import { api, state } from "./api.js";

const SOUND_KEY = "rest_sound";
let audio = null;
let registration = null;

export const store = {
  get(key) {
    try {
      return localStorage.getItem(key);
    } catch {
      return null;
    }
  },
  set(key, value) {
    try {
      if (value === null) localStorage.removeItem(key);
      else localStorage.setItem(key, value);
    } catch {
      // 保存できなくても続ける
    }
  },
};

export const soundOn = () => store.get(SOUND_KEY) !== "off";
export const setSound = (on) => store.set(SOUND_KEY, on ? "on" : "off");

// 音は、ボタンを押したとき（セットの「追加」など）に 用意しておかないと 鳴らせない
export function prepareAudio() {
  try {
    audio ??= new (window.AudioContext || window.webkitAudioContext)();
    if (audio.state === "suspended") audio.resume();
  } catch {
    audio = null;
  }
}

export function beep() {
  if (!soundOn() || !audio) return;
  [0, 0.25, 0.5].forEach((delay) => {
    const osc = audio.createOscillator();
    const gain = audio.createGain();
    osc.type = "square";
    osc.frequency.value = 880;
    gain.gain.setValueAtTime(0.0001, audio.currentTime + delay);
    gain.gain.exponentialRampToValueAtTime(0.25, audio.currentTime + delay + 0.01);
    gain.gain.exponentialRampToValueAtTime(0.0001, audio.currentTime + delay + 0.18);
    osc.connect(gain).connect(audio.destination);
    osc.start(audio.currentTime + delay);
    osc.stop(audio.currentTime + delay + 0.2);
  });
}

export function vibrate() {
  try {
    navigator.vibrate?.([200, 100, 200, 100, 200]); // Android だけ（iPhone は ふるえさせられない）
  } catch {
    // 使えなくても続ける
  }
}

// ---- 通知（画面を消していても出る知らせ）----

export async function registerWorker() {
  if (!("serviceWorker" in navigator)) return null;
  try {
    registration = await navigator.serviceWorker.register("/sw.js");
  } catch {
    registration = null;
  }
  return registration;
}

// このスマホ・ブラウザで 通知が使えるか
export function pushSupport() {
  if (!window.isSecureContext) return "insecure"; // https か localhost でないと 使えない
  if (!("serviceWorker" in navigator) || !("PushManager" in window) || !("Notification" in window)) {
    const ios = /iPhone|iPad|iPod/.test(navigator.userAgent);
    return ios ? "ios-needs-home" : "unsupported"; // iPhone は ホーム画面に追加すると 使える
  }
  return "ok";
}

export async function pushEnabled() {
  if (pushSupport() !== "ok" || Notification.permission !== "granted" || !registration) return false;
  return Boolean(await registration.pushManager.getSubscription());
}

function keyBytes(base64) {
  const text = atob(base64.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (base64.length % 4)) % 4));
  return Uint8Array.from(text, (c) => c.charCodeAt(0));
}

// 通知をオンにする（ボタンを押したときに呼ぶ。許可を聞く画面が出る）
export async function enablePush() {
  if (pushSupport() !== "ok") throw new Error("このブラウザでは 通知が使えません");
  if (!state.vapidKey) throw new Error("サーバーの通知の準備が できていません");
  registration ??= await registerWorker();
  if (!registration) throw new Error("通知の準備が できませんでした");
  const permission = await Notification.requestPermission();
  if (permission !== "granted") throw new Error("通知が許可されませんでした（ブラウザの設定で 許可してね）");
  await navigator.serviceWorker.ready;
  let sub = await registration.pushManager.getSubscription();
  sub ??= await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(state.vapidKey) });
  await api("/push/subscribe", { method: "POST", body: sub.toJSON() });
  return true;
}

export async function disablePush() {
  const sub = await registration?.pushManager.getSubscription();
  if (!sub) return;
  await api("/push/unsubscribe", { method: "POST", body: sub.toJSON() }).catch(() => {});
  await sub.unsubscribe();
}

// 画面の中から 通知を出す（タイマーが 動いたまま 画面がかくれているとき）
export function showNotification(title, body, tag) {
  if (!registration?.active || !("Notification" in window) || Notification.permission !== "granted") return;
  registration.active.postMessage({ type: "notify", title, body, tag });
}
