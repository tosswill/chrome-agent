// Read the transcript panel's segments (the panel is opened by trusted clicks from Python).
// YouTube can render the transcript in two places (the engagement panel and a description section),
// so segments are read from one container only (the visible one with the most segments), then deduped.
(() => {
  const sel = "transcript-segment-view-model, ytd-transcript-segment-renderer";
  const toSec = t => t.split(":").reduce((a, b) => a * 60 + +b, 0);
  const all = [...document.querySelectorAll(sel)];
  const groups = new Map();
  for (const s of all) {
    const c = s.closest("ytd-engagement-panel-section-list-renderer, ytd-transcript-renderer, ytd-video-description-transcript-section-renderer, ytd-macro-markers-list-renderer") || s.parentElement;
    if (!groups.has(c)) groups.set(c, []);
    groups.get(c).push(s);
  }
  const visible = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const pick = [...groups.entries()].sort((a, b) => (b[1].length - a[1].length) || (visible(b[0]) - visible(a[0])))[0];
  const seen = new Set();
  const segments = (pick ? pick[1] : []).map(s => {
    const at = (s.querySelector(".ytwTranscriptSegmentViewModelTimestamp, .segment-timestamp")?.textContent || "").trim();
    const text = (s.querySelector(".ytAttributedStringHost, .segment-text")?.textContent || "").replace(/\s+/g, " ").trim();
    return { at, sec: at ? toSec(at) : null, text };
  }).filter(s => s.text && !seen.has(s.sec + "|" + s.text) && seen.add(s.sec + "|" + s.text));
  const panel = pick ? pick[0] : document;
  const language = (panel.querySelector?.("ytd-transcript-footer-renderer #label-text, ytd-transcript-footer-renderer yt-dropdown-menu, yt-sort-filter-sub-menu-renderer #label-text")
    ?.textContent || "").replace(/\s+/g, " ").trim().replace(/^(.+?) \1$/, "$1") || null;
  return { available: segments.length > 0, segments, containers: groups.size, rendered_segments: all.length, language };
})()
