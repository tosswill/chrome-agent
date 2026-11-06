// Read up to opts.max loaded top-level comment threads (scrolled into view from Python).
// `total` is YouTube's count, which includes replies, so it can exceed the threads returned.
((opts) => {
  const threads = [...document.querySelectorAll("ytd-comment-thread-renderer")].slice(0, opts.max);
  return {
    available: threads.length > 0,
    threads_loaded: document.querySelectorAll("ytd-comment-thread-renderer").length,
    total: (() => { const t = document.querySelector("ytd-comments-header-renderer #count")?.textContent || "";
      const m = t.replace(/,/g, "").match(/[\d.]+/); return m ? +m[0] : null; })(),
    comments: threads.map(t => ({
      author: t.querySelector("#author-text")?.textContent.trim() ?? null,
      likes: t.querySelector("#vote-count-middle")?.textContent.trim() || "0",
      age: t.querySelector("#published-time-text")?.textContent.trim() ?? null,
      pinned: !!t.querySelector("ytd-pinned-comment-badge-renderer:not([hidden])"),
      creator_heart: !!t.querySelector("#creator-heart ytd-creator-heart-renderer, #creator-heart-button ytd-creator-heart-renderer"),
      replies: t.querySelector("#more-replies button")?.innerText.trim() || null,
      text: t.querySelector("#content-text")?.innerText.trim() ?? "",
      link: (() => { const a = t.querySelector("#published-time-text a, a#published-time-text, #header-author a[href*='lc=']");
        return a ? a.href : null; })(),
    })),
  };
})
