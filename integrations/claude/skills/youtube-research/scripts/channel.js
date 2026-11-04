// Header and recent uploads on a channel's /videos page.
(() => {
  const items = [...document.querySelectorAll("ytd-rich-item-renderer")].map(i => {
    const a = i.querySelector("a#video-title-link, a[href*='watch?v=']");
    const meta = [...i.querySelectorAll(".ytContentMetadataViewModelMetadataText, #metadata-line span")].map(s => s.textContent.trim()).filter(Boolean);
    const title = (i.querySelector("#video-title, h3")?.textContent || "").trim();
    return a ? { title, url: a.href.split("&")[0], views: meta[0] ?? null, age: meta[1] ?? null,
                 length: i.querySelector("badge-shape, .yt-badge-shape__text, ytd-thumbnail-overlay-time-status-renderer")?.innerText.trim() ?? null } : null;
  }).filter(Boolean);
  const header = document.querySelector("yt-content-metadata-view-model, #channel-header-container")?.innerText.replace(/\s+/g, " ").trim() ?? null;
  const num = t => { if (!t) return null; const m = t.replace(/,/g, "").match(/([\d.]+)\s*([KMB])?/i);
    return m ? Math.round(parseFloat(m[1]) * ({K: 1e3, M: 1e6, B: 1e9}[(m[2] || "").toUpperCase()] || 1)) : null; };
  const subs = header?.match(/([\d.,]+\s*[KMB]?)\s+subscribers?/i)?.[1];
  const vids = header?.match(/([\d.,]+\s*[KMB]?)\s+videos?/i)?.[1];
  return {
    name: document.querySelector("yt-dynamic-text-view-model h1, #channel-name #text")?.innerText.trim() ?? null,
    handle: header?.match(/@[\w.-]+/)?.[0] ?? null,
    subscribers: num(subs), videos: num(vids),
    description: document.querySelector("yt-description-preview-view-model, #channel-tagline")?.innerText.replace(/\s+/g, " ").replace(/\s*\.\.\.\s*more$/i, "").trim() ?? null,
    links: [...document.querySelectorAll("yt-attribution-view-model a, #channel-header-links a, yt-page-header-view-model a[href*='redirect'], yt-page-header-view-model a[href^='http']")]
      .map(a => a.innerText.trim() || a.href).filter((v, i, arr) => v && arr.indexOf(v) === i).slice(0, 10),
    header,
    url: location.href,
    recent_uploads: items,
  };
})()
