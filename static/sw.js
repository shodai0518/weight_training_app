// プッシュ通知を受け取って 画面に出す（アプリを閉じていても 動く部分）

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch {
    data = {};
  }
  event.waitUntil(
    self.registration.showNotification(data.title || "筋トレ", {
      body: data.body || "",
      tag: data.tag || "app", // 同じ種類の知らせは 上書きする（休憩おわりが いくつも たまらない）
      renotify: true,
      icon: "/icon.svg",
      vibrate: [200, 100, 200, 100, 200],
      data: { url: data.url || "/" },
    }),
  );
});

// 画面の中から頼まれたときに 知らせを出す（タイマーが 画面の中で終わったとき）
self.addEventListener("message", (event) => {
  const data = event.data || {};
  if (data.type === "notify") {
    self.registration.showNotification(data.title, {
      body: data.body || "", tag: data.tag || "app", renotify: true, icon: "/icon.svg",
      vibrate: [200, 100, 200, 100, 200], data: { url: "/" },
    });
  }
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = event.notification.data?.url || "/";
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((windows) => {
      const open = windows[0];
      if (open) {
        open.focus();
        if (url !== "/") open.navigate(url);
        return;
      }
      return self.clients.openWindow(url);
    }),
  );
});
