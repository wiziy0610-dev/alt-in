/* ============================================================
   Alt-In Service Worker
   전략: Cache-First (정적 자산) + Network-First (뉴스 JSON)
   ============================================================ */

const CACHE_VERSION = "v1";
const STATIC_CACHE  = `alt-in-static-${CACHE_VERSION}`;
const NEWS_CACHE    = `alt-in-news-${CACHE_VERSION}`;

// 앱 껍데기(App Shell) — 설치 시 즉시 캐시
const APP_SHELL = [
  "/",
  "/index.html",
  "/manifest.json",
  "/icons/icon-192.png",
  "/icons/icon-512.png",
];

// 뉴스 데이터 엔드포인트 (스크래퍼가 생성하는 JSON)
const NEWS_URL = "/alt_in_news.json";

// ── 설치 ──────────────────────────────────────────────────────
self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(STATIC_CACHE).then((cache) => cache.addAll(APP_SHELL))
  );
  self.skipWaiting(); // 대기 없이 즉시 활성화
});

// ── 활성화 (이전 캐시 정리) ───────────────────────────────────
self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((k) => k !== STATIC_CACHE && k !== NEWS_CACHE)
          .map((k) => caches.delete(k))
      )
    )
  );
  self.clients.claim();
});

// ── Fetch 가로채기 ────────────────────────────────────────────
self.addEventListener("fetch", (e) => {
  const { url } = e.request;

  // 뉴스 JSON → Network-First (오프라인 시 캐시 폴백)
  if (url.includes(NEWS_URL)) {
    e.respondWith(networkFirstStrategy(e.request, NEWS_CACHE));
    return;
  }

  // 정적 자산 → Cache-First
  e.respondWith(cacheFirstStrategy(e.request, STATIC_CACHE));
});

// ── 전략 함수 ─────────────────────────────────────────────────

/** Cache-First: 캐시 히트 → 캐시 반환 / 미스 → 네트워크 후 캐시 저장 */
async function cacheFirstStrategy(request, cacheName) {
  const cached = await caches.match(request);
  if (cached) return cached;

  try {
    const response = await fetch(request);
    if (response.ok) {
      const cache = await caches.open(cacheName);
      cache.put(request, response.clone());
    }
    return response;
  } catch {
    // 오프라인 폴백: 최상위 내비게이션은 index.html로
    if (request.mode === "navigate") {
      return caches.match("/index.html");
    }
    return new Response("오프라인 상태입니다.", { status: 503 });
  }
}

/** Network-First: 네트워크 성공 → 캐시 갱신 / 실패 → 캐시 반환 */
async function networkFirstStrategy(request, cacheName) {
  try {
    const response = await fetch(request);
    if (response.ok) {
      const cache = await caches.open(cacheName);
      cache.put(request, response.clone());
    }
    return response;
  } catch {
    const cached = await caches.match(request);
    return cached ?? new Response(JSON.stringify([]), {
      headers: { "Content-Type": "application/json" },
    });
  }
}

// ── 백그라운드 주기적 갱신 (Background Sync) ─────────────────
self.addEventListener("periodicsync", (e) => {
  if (e.tag === "news-refresh") {
    e.waitUntil(refreshNews());
  }
});

async function refreshNews() {
  try {
    const response = await fetch(NEWS_URL, { cache: "no-store" });
    if (response.ok) {
      const cache = await caches.open(NEWS_CACHE);
      await cache.put(NEWS_URL, response.clone());

      // 열려 있는 탭에 갱신 알림 전송
      const clients = await self.clients.matchAll({ type: "window" });
      clients.forEach((client) => client.postMessage({ type: "NEWS_UPDATED" }));
    }
  } catch (err) {
    console.warn("[SW] 뉴스 갱신 실패:", err);
  }
}

// ── 푸시 알림 (선택) ──────────────────────────────────────────
self.addEventListener("push", (e) => {
  const data = e.data?.json() ?? { title: "Alt-In", body: "새 기사가 업데이트됐어요." };
  e.waitUntil(
    self.registration.showNotification(data.title, {
      body: data.body,
      icon: "/icons/icon-192.png",
      badge: "/icons/icon-72.png",
      tag: "alt-in-news",
      renotify: true,
    })
  );
});

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  e.waitUntil(
    self.clients.matchAll({ type: "window" }).then((clients) => {
      if (clients.length) return clients[0].focus();
      return self.clients.openWindow("/");
    })
  );
});
