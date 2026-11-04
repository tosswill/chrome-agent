// Read the transcript panel's segments (the panel is opened by trusted clicks from Python).
(() => {
  const sel = "transcript-segment-view-model, ytd-transcript-segment-renderer";
  const toSec = t => t.split(":").reduce((a, b) => a * 60 + +b, 0);
  const segments = [...document.querySelectorAll(sel)].map(s => {
    const at = (s.querySelector(".ytwTranscriptSegmentViewModelTimestamp, .segment-timestamp")?.textContent || "").trim();
    const text = (s.querySelector(".ytAttributedStringHost, .segment-text")?.textContent || "").replace(/\s+/g, " ").trim();
    return { at, sec: at ? toSec(at) : null, text };
  }).filter(s => s.text);
  return { available: segments.length > 0, segments };
})()
