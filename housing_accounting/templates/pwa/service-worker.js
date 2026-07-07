{% load static %}
const CACHE_NAME = "housynk-pwa-v2";
const PRECACHE_URLS = [
  "{% url 'pwa-offline' %}",
  "{% url 'pwa-manifest' %}",
  "{% static 'images/favicons/pwa-icon-192.png' %}",
  "{% static 'images/favicons/pwa-icon-512.png' %}",
  "{% static 'images/favicons/apple-touch-icon.png' %}",
  "{% static 'images/brand/housynk-logo-mark-transparent.png' %}",
  "{% static 'images/brand/housynk-logo-square-transparent.png' %}",
  "{% static 'images/brand/housynk-logo-horizontal-transparent.png' %}",
];

const isSameOrigin = (requestUrl) => new URL(requestUrl).origin === self.location.origin;
const isHtmlRequest = (request) =>
  request.mode === "navigate" ||
  request.headers.get("accept")?.includes("text/html") ||
  request.headers.get("hx-request") === "true";

const cacheResponse = (request, response) => {
  if (!response || !response.ok) {
    return response;
  }

  const responseClone = response.clone();
  caches.open(CACHE_NAME).then((cache) => cache.put(request, responseClone));
  return response;
};

const networkFirstHtml = async (request) => {
  try {
    const networkResponse = await fetch(request);
    return cacheResponse(request, networkResponse);
  } catch (error) {
    const cachedResponse = await caches.match(request);
    if (cachedResponse) {
      return cachedResponse;
    }

    return caches.match("{% url 'pwa-offline' %}") || Response.error();
  }
};

const cacheFirstStatic = async (request) => {
  const cachedResponse = await caches.match(request);
  if (cachedResponse) {
    return cachedResponse;
  }

  try {
    const networkResponse = await fetch(request);
    return cacheResponse(request, networkResponse);
  } catch (error) {
    return Response.error();
  }
};

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(PRECACHE_URLS)).then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((cacheNames) =>
      Promise.all(
        cacheNames
          .filter((cacheName) => cacheName !== CACHE_NAME)
          .map((cacheName) => caches.delete(cacheName)),
      ),
    ).then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET" || !isSameOrigin(event.request.url)) {
    return;
  }

  const requestUrl = new URL(event.request.url);

  if (isHtmlRequest(event.request)) {
    event.respondWith(networkFirstHtml(event.request));
    return;
  }

  if (!requestUrl.pathname.startsWith("{% static '' %}")) {
    return;
  }

  event.respondWith(cacheFirstStatic(event.request));
});
