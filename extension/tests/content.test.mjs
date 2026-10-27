/*
 * Node tests for the pure parts of content.js.
 * Run with:  node --test extension/tests
 */
import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import path from "node:path";

const require = createRequire(import.meta.url);
const here = path.dirname(fileURLToPath(import.meta.url));
const content = require(path.join(here, "..", "chromium", "content.js"));

test("parseClock understands minutes and hours", () => {
  assert.equal(content.parseClock("1:23"), 83);
  assert.equal(content.parseClock("1:02:03"), 3723);
  assert.equal(content.parseClock("0:00"), 0);
  assert.equal(content.parseClock("nonsense"), null);
  assert.equal(content.parseClock(null), null);
});

test("parseTimeInfo splits position and duration", () => {
  assert.deepEqual(content.parseTimeInfo("1:23 / 4:56"), { position: 83, duration: 296 });
  assert.deepEqual(content.parseTimeInfo("0:05/1:00:00"), { position: 5, duration: 3600 });
  assert.equal(content.parseTimeInfo(""), null);
});

test("extractVideoId finds ids in watch URLs and thumbnails", () => {
  assert.equal(content.extractVideoId("https://music.youtube.com/watch?v=dQw4w9WgXcQ&list=RD"),
    "dQw4w9WgXcQ");
  assert.equal(content.extractVideoId("https://i.ytimg.com/vi/dQw4w9WgXcQ/maxresdefault.jpg"),
    "dQw4w9WgXcQ");
  assert.equal(content.extractVideoId("https://music.youtube.com/library"), null);
});

test("upsizeArtworkUrl asks the CDN for a larger image", () => {
  assert.equal(content.upsizeArtworkUrl("https://lh3.googleusercontent.com/abc=w60-h60-l90-rj"),
    "https://lh3.googleusercontent.com/abc=w544-h544-l90-rj");
  assert.equal(content.upsizeArtworkUrl("https://example.com/x=s120"), "https://example.com/x=s544");
  assert.equal(content.upsizeArtworkUrl(null), null);
});

test("pickArtwork prefers the largest Media Session image", () => {
  const artwork = [
    { src: "https://x/small=w60-h60", sizes: "60x60" },
    { src: "https://x/large=w120-h120", sizes: "120x120" },
  ];
  assert.equal(content.pickArtwork(artwork, "https://x/dom=w60-h60"),
    "https://x/large=w544-h544");
  assert.equal(content.pickArtwork(null, "https://x/dom=w60-h60"), "https://x/dom=w544-h544");
  assert.equal(content.pickArtwork([], null), null);
});

test("buildPacket produces the packet the companion expects", () => {
  const packet = content.buildPacket({
    title: "Song Title",
    artist: "Artist Name",
    album: "Album Name",
    videoId: "dQw4w9WgXcQ",
    artwork: "https://x/art=w544-h544",
    playing: true,
    position: 12,
    duration: 200,
  }, 1_700_000_000_000);

  assert.equal(packet.jsApplicationType, "youtubeMusic");
  assert.equal(packet.jsTitle, "Song Title");
  assert.equal(packet.jsAuthor, "Artist Name");
  assert.equal(packet.jsAlbum, "Album Name");
  assert.equal(packet.jsVideoId, "dQw4w9WgXcQ");
  assert.equal(packet.jsVideoUrl, "https://www.youtube.com/watch?v=dQw4w9WgXcQ");
  assert.equal(packet.jsPlaybackState, "playing");
  assert.equal(packet.jsPositionSec, 12);
  assert.equal(packet.jsDurationSec, 200);
  assert.equal(packet.presenceData.type, 2);
  assert.equal(packet.presenceData.details, "Song Title");
  assert.equal(packet.presenceData.state, "by Artist Name");
  assert.equal(packet.presenceData.assets.large_image, "https://x/art=w544-h544");
  assert.equal(packet.presenceData.assets.large_text, "Album Name");
  assert.equal(packet.presenceData.timestamps.start, 1_700_000_000_000 - 12_000);
  assert.equal(packet.presenceData.timestamps.end,
    1_700_000_000_000 - 12_000 + 200_000);
});

test("paused packets carry no timestamps", () => {
  const packet = content.buildPacket({
    title: "Song", artist: "Artist", album: "", videoId: null,
    artwork: null, playing: false, position: 30, duration: 100,
  }, 1_700_000_000_000);
  assert.equal(packet.jsPlaybackState, "paused");
  assert.equal(packet.presenceData.timestamps, null);
});

test("adverts and empty titles produce no packet", () => {
  assert.equal(content.buildPacket({ title: "", playing: true }), null);
  assert.equal(content.buildPacket({ title: "Song", playing: true, adPlaying: true }), null);
  assert.equal(content.buildPacket(null), null);
});

test("idlePacket uses the shared sentinel", () => {
  const packet = content.idlePacket();
  assert.equal(packet.presenceData, content.IDLE_SENTINEL);
  assert.equal(packet.presenceData, "#*IDLE*#");
});

function fakeList(items) {
  return items;
}

test("readByline separates artist and album links", () => {
  const byline = {
    querySelectorAll: (selector) => (selector === "a"
      ? fakeList([{ textContent: "Artist Name - Topic" }, { textContent: "Album Name" }])
      : []),
    textContent: "Artist Name - Topic • Album Name • 2020",
  };
  const bar = { querySelector: (selector) => (selector === ".byline" ? byline : null) };
  const result = content.readByline(bar);
  assert.equal(result.artist, "Artist Name");
  assert.equal(result.album, "Album Name");
  assert.deepEqual(content.readByline(null), { artist: "", album: "" });
});

test("readState reads the player bar, the video element and the URL", () => {
  const titleElement = {
    textContent: "Song Title",
    closest: () => null,
  };
  const bar = {
    querySelector: (selector) => {
      if (selector === ".title") return titleElement;
      if (selector === ".byline") {
        return { querySelectorAll: () => [{ textContent: "Artist" }, { textContent: "Album" }],
                 textContent: "Artist • Album" };
      }
      if (selector === ".time-info") return { textContent: "1:00 / 3:20" };
      if (selector === "img") return { src: "https://x/art=w60-h60" };
      return null;
    },
  };
  const document = {
    querySelector: (selector) => {
      if (selector === "ytmusic-player-bar") return bar;
      if (selector === "video") return { currentTime: 61.5, duration: 200.25, paused: false, ended: false };
      return null;
    },
  };
  const state = content.readState(document,
    { href: "https://music.youtube.com/watch?v=dQw4w9WgXcQ" }, null);
  assert.equal(state.title, "Song Title");
  assert.equal(state.artist, "Artist");
  assert.equal(state.album, "Album");
  assert.equal(state.videoId, "dQw4w9WgXcQ");
  assert.equal(state.playing, true);
  assert.equal(state.position, 61.5);
  assert.equal(state.duration, 200.25);
  assert.equal(state.artwork, "https://x/art=w544-h544");
});

test("readState falls back to media session metadata and marks adverts", () => {
  const document = {
    querySelector: (selector) => {
      if (selector === "video") return { currentTime: 0, duration: 0, paused: true, ended: false };
      if (selector === ".ad-showing") return {};
      return null;
    },
  };
  const mediaSession = {
    metadata: { title: "MS Title", artist: "MS Artist", album: "MS Album",
                artwork: [{ src: "https://x/ms=w120-h120", sizes: "120x120" }] },
    playbackState: "paused",
  };
  const state = content.readState(document, { href: "https://music.youtube.com/" }, mediaSession);
  assert.equal(state.title, "MS Title");
  assert.equal(state.artist, "MS Artist");
  assert.equal(state.album, "MS Album");
  assert.equal(state.playing, false);
  assert.equal(state.adPlaying, true);
});
