// Unique posts on a Reddit search or listing page, deduped by post ID, in page order (read-only).
// Search results carry engagement (score, comments, created), the author and subreddit, and
// whether the hit matched the post itself or one of its comments (match: "comment", with the
// matching comment's text in `snippet`). Promoted (ad) units are skipped.
(() => {
  const abs = u => (u ? new URL(u, location.origin).href : null);
  const num = e => (e && e.getAttribute("number") !== null ? +e.getAttribute("number") : null);
  // Tracking context on each search unit: the real post title, author, subreddit, comment snippet.
  const ctx = new Map();
  for (const t of document.querySelectorAll("search-telemetry-tracker[data-faceplate-tracking-context]")) {
    let c;
    try { c = JSON.parse(t.getAttribute("data-faceplate-tracking-context")); } catch (e) { continue; }
    const id = (c.post?.id || "").replace(/^t3_/, "");
    if (!id) continue;
    const e = ctx.get(id) || {};
    e.title = e.title || c.post?.title || null;
    e.author = e.author || c.profile?.name || null;
    e.sub = e.sub || c.subreddit?.name || null;
    e.nsfw = e.nsfw || !!c.post?.nsfw;
    if ((c.search?.snippet_id || "").startsWith("t1_") && !e.snippet_id) {
      e.snippet = c.search.snippet || "";
      e.snippet_id = c.search.snippet_id;
    }
    ctx.set(id, e);
  }
  const isAd = el => !!el.closest("shreddit-ad-post, [data-testid*='promoted'], [data-promoted='true']");
  const out = new Map();
  const add = (id, rec) => { if (!out.has(id)) out.set(id, rec); };

  // 1. Search result units.
  for (const a of document.querySelectorAll('a[data-testid="post-title-text"], a[data-testid="post-title"]')) {
    const m = (a.getAttribute("href") || "").match(/\/r\/([^/]+)\/comments\/([a-z0-9]+)/i);
    if (!m || out.has(m[2]) || isAd(a)) continue;
    let unit = a;
    for (let i = 0; i < 8 && unit && !unit.querySelector('[data-testid="search-counter-row"]'); i++) unit = unit.parentElement;
    const row = unit?.querySelector('[data-testid="search-counter-row"]');
    let score = null, comments = null;
    if (row) {
      for (const span of row.querySelectorAll("span")) {
        const n = num(span.querySelector(":scope > faceplate-number"));
        if (n === null) continue;
        if (/comment/i.test(span.textContent)) comments = n; else if (/vote|point|upvote/i.test(span.textContent)) score = n;
      }
    }
    const c = ctx.get(m[2]) || {};
    const ts = unit?.querySelector("faceplate-timeago[ts]")?.getAttribute("ts") || null;
    const rec = {
      id: m[2], sub: c.sub || m[1], url: `https://www.reddit.com/r/${c.sub || m[1]}/comments/${m[2]}/`,
      title: (c.title || a.innerText || "").trim(), author: c.author || null,
      score, comments, created: ts, match: c.snippet_id ? "comment" : "post",
    };
    if (c.snippet_id) {
      rec.snippet = c.snippet;
      rec.snippet_link = `https://www.reddit.com/r/${c.sub || m[1]}/comments/${m[2]}/comment/${c.snippet_id.slice(3)}/`;
    }
    if (c.nsfw) rec.nsfw = true;
    add(m[2], rec);
  }

  // 2. Feed/listing posts (subreddit pages, `search --url` listings).
  for (const p of document.querySelectorAll("shreddit-post")) {
    const id = (p.getAttribute("id") || "").replace(/^t3_/, "");
    if (!id || out.has(id) || isAd(p) || p.hasAttribute("promoted")) continue;
    const sub = (p.getAttribute("subreddit-prefixed-name") || "").replace(/^r\//, "");
    add(id, {
      id, sub, url: abs(p.getAttribute("permalink")) || `https://www.reddit.com/r/${sub}/comments/${id}/`,
      title: p.getAttribute("post-title") || "", author: p.getAttribute("author"),
      score: p.getAttribute("score") !== null ? +p.getAttribute("score") : null,
      comments: p.getAttribute("comment-count") !== null ? +p.getAttribute("comment-count") : null,
      created: p.getAttribute("created-timestamp"), match: "post",
    });
  }

  // 3. Anything else that links to a thread (older layouts), longest link text as the title.
  const loose = new Map();
  for (const a of document.querySelectorAll('a[href*="/comments/"]')) {
    if (a.href.includes("games_drawer") || isAd(a)) continue;
    const m = a.href.match(/\/r\/([^/]+)\/comments\/([a-z0-9]+)/i);
    if (!m || out.has(m[2])) continue;
    const text = a.innerText.trim();
    const prev = loose.get(m[2]);
    if (!prev || text.length > prev.title.length)
      loose.set(m[2], { id: m[2], sub: m[1], url: `https://www.reddit.com/r/${m[1]}/comments/${m[2]}/`, title: text,
                        author: null, score: null, comments: null, created: null, match: "post" });
  }
  for (const [id, rec] of loose) add(id, rec);
  return [...out.values()];
})()
