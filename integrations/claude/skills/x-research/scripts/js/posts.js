// Posts (tweets) currently rendered on an X timeline, search, thread or profile page.
// X virtualizes timelines, so the helper calls this after each scroll and merges by URL.
(() => {
  const num = (s) => {
    if (!s) return null;
    const m = String(s).replace(/,/g, "").match(/([\d.]+)\s*([KMB])?/i);
    if (!m) return null;
    const mult = {K: 1e3, M: 1e6, B: 1e9}[(m[2] || "").toUpperCase()] || 1;
    return Math.round(parseFloat(m[1]) * mult);
  };
  const counts = (label) => {
    const out = {};
    for (const [k, re] of Object.entries({replies: /([\d,]+) repl/i, reposts: /([\d,]+) repost/i, likes: /([\d,]+) like/i,
                                          bookmarks: /([\d,]+) bookmark/i, views: /([\d,]+) view/i})) {
      const m = (label || "").match(re);
      out[k] = m ? num(m[1]) : 0;
    }
    return out;
  };
  const author = (root) => {
    const un = root.querySelector("[data-testid=User-Name]");
    if (!un) return {};
    const handle = [...un.querySelectorAll("a[href^='/']")].map(a => a.getAttribute("href")).find(h => /^\/\w+$/.test(h));
    return {name: (un.innerText.split("\n")[0] || "").trim(), handle: handle ? "@" + handle.slice(1) : null,
            verified: !!un.querySelector("[data-testid=icon-verified]")};
  };
  const out = [];
  for (const a of document.querySelectorAll("article[data-testid=tweet]")) {
    // The quoted post sits in a nested div[role=link]; read the outer post's parts only.
    const quoteBox = [...a.querySelectorAll("div[role=link]")].find(d => d.querySelector("[data-testid=User-Name]"));
    const own = (sel) => [...a.querySelectorAll(sel)].find(e => !quoteBox || !quoteBox.contains(e));
    const time = own("time");
    const link = time && time.closest("a");
    const textEl = own("[data-testid=tweetText]");
    const spans = [...a.querySelectorAll("span")].map(s => s.innerText.trim());
    const replying = [...a.querySelectorAll("div")].find(d => /^Replying to\s/.test(d.innerText || "") && d.children.length < 6);
    const p = {
      url: link ? "https://x.com" + link.getAttribute("href").replace(/\/(analytics|media_tags|photo\/\d+)$/, "") : null,
      ...author(a),
      date: time ? time.getAttribute("datetime") : null,
      text: textEl ? textEl.innerText : "",
      lang: textEl ? textEl.getAttribute("lang") : null,
      ...counts(a.querySelector("[role=group]")?.getAttribute("aria-label")),
      media: {photos: a.querySelectorAll("[data-testid=tweetPhoto] img").length,
              video: !!own("[data-testid=videoPlayer]"), card: !!own("[data-testid='card.wrapper']")},
      reposted_by: a.querySelector("[data-testid=socialContext]")?.innerText || null,
      replying_to: replying ? replying.innerText.replace(/^Replying to\s+/, "").trim() : null,
      promoted: !link || spans.includes("Ad") || spans.includes("Promoted"),
      truncated: !!a.querySelector("[data-testid=tweet-text-show-more-link]"),
      community_note: /Readers added context/i.test(a.innerText),
    };
    if (quoteBox) {
      const qt = quoteBox.querySelector("[data-testid=tweetText]");
      p.quoted = {...author(quoteBox), text: qt ? qt.innerText : "", date: quoteBox.querySelector("time")?.getAttribute("datetime") || null};
    }
    out.push(p);
  }
  return out;
})()
