// Videos (and optionally Shorts) on a YouTube search results page. Ads are skipped.
((opts) => {
  const clean = h => { try { const u = new URL(h, location.origin); u.searchParams.delete("pp"); return u.href; } catch { return h; } };
  const out = [];
  for (const v of document.querySelectorAll("ytd-video-renderer")) {
    const a = v.querySelector("a#video-title");
    if (!a) continue;
    const href = clean(a.href);
    const isShort = href.includes("/shorts/");
    if (isShort && !opts.shorts) continue;
    const meta = [...v.querySelectorAll("#metadata-line span, .inline-metadata-item")].map(s => s.textContent.trim()).filter(Boolean);
    out.push({
      type: isShort ? "short" : "video",
      title: a.textContent.trim(),
      url: href,
      channel: v.querySelector("ytd-channel-name #text, #channel-name #text")?.textContent.trim() ?? null,
      channel_url: v.querySelector("ytd-channel-name a, #channel-name a")?.href ?? null,
      verified: !!v.querySelector(".badge-style-type-verified, [aria-label='Verified']"),
      views: meta[0] ?? null,
      age: meta[1] ?? null,
      length: v.querySelector("badge-shape .yt-badge-shape__text, ytd-thumbnail-overlay-time-status-renderer #text, .badge-shape-wiz__text")?.textContent.trim() ?? null,
    });
  }
  if (opts.shorts) {
    for (const s of document.querySelectorAll("ytm-shorts-lockup-view-model")) {
      const a = s.querySelector("a[href*='/shorts/']");
      if (!a) continue;
      out.push({ type: "short", title: (s.querySelector("h3")?.innerText || a.title || "").trim(),
                 url: clean(a.href), views: s.innerText.split("\n").find(t => /views?$/i.test(t.trim())) ?? null });
    }
  }
  const seen = new Set();
  return out.filter(v => !seen.has(v.url) && seen.add(v.url));
})
