/*
 * Music RPC - YouTube Music presence collector.
 *
 * Runs only on music.youtube.com. Reads the player bar, the <video> element and
 * the Media Session metadata, then forwards a packet with the same shape the
 * Windows build of Music RPC already understands:
 *
 *   { jsApplicationType, jsTitle, jsAuthor, jsAlbum, jsVideoUrl, jsVideoId,
 *     jsPlaybackState, jsPositionSec, jsDurationSec, presenceData }
 *
 * jsPlaybackState / jsPositionSec / jsDurationSec were added for the Linux port
 * so that presence also works when the browser exposes no MPRIS player; older
 * companions simply ignore them.
 *
 * The pure helpers are exported for the Node test-suite when `module` exists.
 */
(function () {
  "use strict";

  var NATIVE_APPLICATION_TYPE = "youtubeMusic";
  var IDLE_SENTINEL = "#*IDLE*#";
  var HEARTBEAT_PLAYING_MS = 2000;
  var HEARTBEAT_PAUSED_MS = 5000;

  /* ---------------------------------------------------------------- helpers */

  function parseTimeInfo(text) {
    if (!text) return null;
    var parts = String(text).split("/");
    var position = parseClock(parts[0]);
    var duration = parts.length > 1 ? parseClock(parts[1]) : null;
    if (position === null && duration === null) return null;
    return { position: position, duration: duration };
  }

  function parseClock(value) {
    if (value === null || value === undefined) return null;
    var match = String(value).trim().match(/^(\d+):(\d{1,2})(?::(\d{1,2}))?$/);
    if (!match) return null;
    if (match[3] !== undefined) {
      return Number(match[1]) * 3600 + Number(match[2]) * 60 + Number(match[3]);
    }
    return Number(match[1]) * 60 + Number(match[2]);
  }

  function extractVideoId(url) {
    if (!url) return null;
    var match = String(url).match(/[?&]v=([A-Za-z0-9_-]{11})/);
    if (match) return match[1];
    match = String(url).match(/\/vi\/([A-Za-z0-9_-]{11})/);
    if (match) return match[1];
    return null;
  }

  function videoIdFromHref(href) {
    if (!href) return null;
    var match = String(href).match(/[?&]v=([A-Za-z0-9_-]{11})/);
    return match ? match[1] : null;
  }

  /* Ask the image CDN for a bigger crop of the same picture. */
  function upsizeArtworkUrl(url) {
    if (!url) return null;
    return String(url)
      .replace(/=w\d+-h\d+/, "=w544-h544")
      .replace(/=s\d+(-[a-z0-9-]+)?$/i, "=s544");
  }

  function pickArtwork(mediaSessionArtwork, imgSrc) {
    if (mediaSessionArtwork && mediaSessionArtwork.length) {
      var best = null;
      for (var index = 0; index < mediaSessionArtwork.length; index += 1) {
        var item = mediaSessionArtwork[index];
        if (!item || !item.src) continue;
        var size = Number(item.sizes ? String(item.sizes).split("x")[0] : 0) || 0;
        if (!best || size > best.size) best = { src: item.src, size: size };
      }
      if (best) return upsizeArtworkUrl(best.src);
    }
    if (imgSrc) return upsizeArtworkUrl(imgSrc);
    return null;
  }

  function cleanText(value) {
    if (value === null || value === undefined) return "";
    return String(value).replace(/\s+/g, " ").trim();
  }

  function stripChannelSuffix(artist) {
    return cleanText(artist).replace(/\s*-\s*Topic$/i, "");
  }

  /* Build the packet that both companions understand. */
  function buildPacket(state, nowMs) {
    var now = nowMs || Date.now();
    if (!state || !state.title || state.adPlaying) return null;
    var duration = Number(state.duration) || 0;
    var position = Math.max(0, Number(state.position) || 0);
    var playing = state.playing !== false;
    var timestamps = null;
    if (playing && duration > 0) {
      var startMs = now - position * 1000;
      timestamps = {
        start: Math.round(startMs),
        end: Math.round(startMs + duration * 1000)
      };
    }
    var presence = {
      type: 2,
      details: cleanText(state.title).slice(0, 128),
      state: state.artist ? ("by " + cleanText(state.artist)).slice(0, 128) : "",
      assets: {},
      timestamps: timestamps
    };
    if (state.artwork) presence.assets.large_image = state.artwork;
    if (state.album) presence.assets.large_text = cleanText(state.album).slice(0, 128);
    var packet = {
      jsApplicationType: NATIVE_APPLICATION_TYPE,
      jsTitle: cleanText(state.title),
      jsAuthor: cleanText(state.artist),
      jsAlbum: cleanText(state.album),
      jsVideoUrl: state.videoId ? "https://www.youtube.com/watch?v=" + state.videoId : "",
      jsVideoId: state.videoId || "",
      jsPlaybackState: playing ? "playing" : "paused",
      jsPositionSec: position,
      jsDurationSec: duration,
      presenceData: presence
    };
    return packet;
  }

  function idlePacket() {
    return { jsApplicationType: NATIVE_APPLICATION_TYPE, presenceData: IDLE_SENTINEL };
  }

  /* ------------------------------------------------------------ DOM readers */

  function readByline(bar) {
    var result = { artist: "", album: "" };
    if (!bar) return result;
    var byline = bar.querySelector(".byline");
    if (!byline) return result;
    var links = byline.querySelectorAll("a");
    if (links.length > 0) result.artist = stripChannelSuffix(links[0].textContent);
    if (links.length > 1) result.album = cleanText(links[1].textContent);
    if (!result.artist) {
      var text = cleanText(byline.textContent).split("\u2022")[0];
      result.artist = stripChannelSuffix(text);
    }
    return result;
  }

  function readState(doc, location, mediaSession) {
    var bar = doc.querySelector("ytmusic-player-bar");
    var video = doc.querySelector("video");
    var titleElement = bar ? bar.querySelector(".title") : null;
    var title = cleanText(titleElement ? titleElement.textContent : "");
    var metadata = (mediaSession && mediaSession.metadata) || null;
    var byline = readByline(bar);
    var artist = byline.artist || stripChannelSuffix(metadata && metadata.artist);
    var album = byline.album || cleanText(metadata && metadata.album);
    if (!title && metadata) title = cleanText(metadata.title);

    var timeInfo = null;
    var timeElement = bar ? bar.querySelector(".time-info") : null;
    if (timeElement) timeInfo = parseTimeInfo(timeElement.textContent);

    var position = video && isFinite(video.currentTime) ? video.currentTime : null;
    var duration = video && isFinite(video.duration) ? video.duration : null;
    if (position === null && timeInfo) position = timeInfo.position;
    if (!duration && timeInfo) duration = timeInfo.duration;

    var artworkImage = bar ? bar.querySelector("img") : null;
    var artwork = pickArtwork(metadata && metadata.artwork,
                              artworkImage ? (artworkImage.src || artworkImage.getAttribute("src")) : null);

    var videoId = extractVideoId(location && location.href);
    if (!videoId) {
      var titleLink = titleElement ? titleElement.closest("a") : null;
      videoId = videoIdFromHref(titleLink ? titleLink.getAttribute("href") : null);
    }
    if (!videoId) videoId = extractVideoId(artwork);

    var playing = true;
    if (video) playing = !video.paused && !video.ended;
    else if (mediaSession && mediaSession.playbackState) {
      playing = mediaSession.playbackState === "playing";
    }

    var adPlaying = !!(doc.querySelector(".ad-showing") ||
                       doc.querySelector("ytmusic-you-there-renderer"));

    return {
      title: title,
      artist: artist,
      album: album,
      videoId: videoId || null,
      artwork: artwork,
      playing: playing,
      position: position === null || position === undefined ? null : position,
      duration: duration === null || duration === undefined ? null : duration,
      adPlaying: adPlaying
    };
  }

  /* --------------------------------------------------------------- wiring */

  function start() {
    var lastSignature = null;
    var lastSentAt = 0;
    var lastTitle = "";
    var idleSent = false;

    function send(payload) {
      try {
        chrome.runtime.sendMessage({ type: "music-rpc:presence", payload: payload });
      } catch (error) {
        /* The service worker is asleep; the next tick will retry. */
      }
    }

    function tick() {
      var state = null;
      try {
        state = readState(document, window.location,
                          typeof navigator !== "undefined" ? navigator.mediaSession : null);
      } catch (error) {
        return;
      }
      if (!state || !state.title) return;
      if (state.adPlaying) return;

      if (!state.playing) {
        /* Pause: send one paused packet, then go quiet. */
        var pausedPacket = buildPacket(state);
        var signature = "paused:" + state.title + "|" + state.artist;
        if (pausedPacket && signature !== lastSignature) {
          lastSignature = signature;
          send(pausedPacket);
        }
        return;
      }

      var packet = buildPacket(state);
      if (!packet) return;
      var playingSignature = "playing:" + state.title + "|" + state.artist + "|" +
                             state.album + "|" + (state.videoId || "");
      var interval = state.playing ? HEARTBEAT_PLAYING_MS : HEARTBEAT_PAUSED_MS;
      var due = playingSignature !== lastSignature ||
                Date.now() - lastSentAt >= interval;
      if (!due) return;
      lastSignature = playingSignature;
      lastSentAt = Date.now();
      idleSent = false;
      if (state.title !== lastTitle) {
        lastTitle = state.title;
      }
      send(packet);
    }

    function sendIdle() {
      if (idleSent) return;
      idleSent = true;
      lastSignature = null;
      send(idlePacket());
    }

    var timer = setInterval(tick, 1000);
    setInterval(function () {
      var video = document.querySelector("video");
      if (!document.hidden && video && video.paused && video.ended) sendIdle();
    }, 15000);

    document.addEventListener("visibilitychange", function () {
      if (!document.hidden) tick();
    });
    window.addEventListener("pagehide", sendIdle);
    window.addEventListener("beforeunload", sendIdle);

    /* React quickly to player-bar updates instead of waiting for the tick. */
    var player = document.querySelector("ytmusic-player-bar");
    if (player && typeof MutationObserver !== "undefined") {
      var observer = new MutationObserver(function () { tick(); });
      observer.observe(player, { subtree: true, childList: true, characterData: true,
                                 attributes: true, attributeFilter: ["src", "title"] });
    }
    setTimeout(tick, 500);
    window.addEventListener("online", tick);
    return timer;
  }

  if (typeof module !== "undefined" && module.exports) {
    module.exports = {
      parseTimeInfo: parseTimeInfo,
      parseClock: parseClock,
      extractVideoId: extractVideoId,
      videoIdFromHref: videoIdFromHref,
      upsizeArtworkUrl: upsizeArtworkUrl,
      pickArtwork: pickArtwork,
      buildPacket: buildPacket,
      idlePacket: idlePacket,
      readByline: readByline,
      readState: readState,
      IDLE_SENTINEL: IDLE_SENTINEL,
      NATIVE_APPLICATION_TYPE: NATIVE_APPLICATION_TYPE
    };
    return;
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start, { once: true });
  } else {
    start();
  }
})();
