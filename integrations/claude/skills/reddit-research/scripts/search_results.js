// Unique posts on a Reddit search/listing page, deduped by post ID.
(() => {
  const seen = new Map();
  for (const a of document.querySelectorAll('a[href*="/comments/"]')) {
    if (a.href.includes("games_drawer")) continue;
    const m = a.href.match(/\/r\/([^/]+)\/comments\/([a-z0-9]+)/i);
    if (!m) continue;
    const text = a.innerText.trim();
    const prev = seen.get(m[2]);
    if (!prev || text.length > prev.title.length)
      seen.set(m[2], { id: m[2], sub: m[1], url: `https://www.reddit.com/r/${m[1]}/comments/${m[2]}/`, title: text });
  }
  return [...seen.values()];
})()
