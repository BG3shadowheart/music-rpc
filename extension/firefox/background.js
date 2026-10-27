/*
 * Music RPC - native messaging bridge.
 *
 * Opens the native messaging host declared in natmsg/com.ytdp.discord.presence.json
 * and forwards the packets produced by content.js. Reconnects with backoff and
 * re-sends the last packet after a reconnect, so a companion restart does not
 * lose the current track.
 */
(function () {
  "use strict";

  var api = typeof browser !== "undefined" ? browser : chrome;
  var NATIVE_HOST = "com.ytdp.discord.presence";
  var RETRY_MIN_MS = 1000;
  var RETRY_MAX_MS = 30000;

  var port = null;
  var retryDelay = RETRY_MIN_MS;
  var retryTimer = null;
  var lastPacket = null;
  var lastResponse = null;
  var nativeVersion = null;

  function scheduleReconnect() {
    if (retryTimer) return;
    retryTimer = setTimeout(function () {
      retryTimer = null;
      retryDelay = Math.min(retryDelay * 2, RETRY_MAX_MS);
      connect();
    }, retryDelay);
  }

  function connect() {
    if (port) return port;
    try {
      port = api.runtime.connectNative(NATIVE_HOST);
    } catch (error) {
      port = null;
      scheduleReconnect();
      return null;
    }
    retryDelay = RETRY_MIN_MS;
    port.onMessage.addListener(function (message) {
      lastResponse = message;
      if (message && message.nativeVersion) nativeVersion = message.nativeVersion;
    });
    port.onDisconnect.addListener(function () {
      /* Reading lastError also suppresses the "unchecked runtime.lastError" log. */
      void api.runtime.lastError;
      port = null;
      scheduleReconnect();
    });
    try {
      port.postMessage({ getNativeVersion: true });
    } catch (error) {
      /* ignored: the next packet will report the failure */
    }
    if (lastPacket) post(lastPacket);
    return port;
  }

  function post(packet) {
    if (!port) connect();
    if (!port) return false;
    try {
      port.postMessage(packet);
      return true;
    } catch (error) {
      port = null;
      scheduleReconnect();
      return false;
    }
  }

  api.runtime.onMessage.addListener(function (message, sender, respond) {
    if (!message || typeof message !== "object") return undefined;
    if (message.type === "music-rpc:presence") {
      lastPacket = message.payload;
      var ok = post(lastPacket);
      if (respond) respond({ ok: ok, nativeVersion: nativeVersion });
      return true;
    }
    if (message.type === "music-rpc:status") {
      if (respond) {
        respond({ connected: !!port, nativeVersion: nativeVersion,
                  lastResponse: lastResponse });
      }
      return true;
    }
    return undefined;
  });

  /* Wake the connection up when the worker starts. */
  connect();
})();
