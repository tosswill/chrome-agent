// Header and recent uploads on a channel's /videos page.
(() => {
  const items = [...document.querySelectorAll("ytd-rich-item-renderer")].map(i => {
    const a = i.querySelector("a#video-title-link, a[href*='watch?v=']");
    const meta = [...i.querySelectorAll(".ytContentMetadataViewModelMetadataText, #metadata-line span")].map(s => s.textContent.trim()).filter(Boolean);
    const title = (i.querySelector("#video-title, h3")?.textContent || "").trim();
    return a ? { title, url: a.href.split("&")[0], views: meta[0] ?? null, age: meta[1] ?? null,
                 length: i.querySelector("badge-shape, .yt-badge-shape__text, ytd-thumbnail-overlay-time-status-renderer")?.innerText.trim() ?? null } : null;
  }).filter(Boolean);
  return {
    name: document.querySelector("yt-dynamic-text-view-model h1, #channel-name #text")?.innerText.trim() ?? null,
    header: document.querySelector("yt-content-metadata-view-model, #channel-header-container")?.innerText.replace(/\s+/g, " ").trim() ?? null,
    url: location.href,
    recent_uploads: items,
  };
})()
