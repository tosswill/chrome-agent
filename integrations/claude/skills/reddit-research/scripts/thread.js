// Post metadata + top-level comments and the OP's depth-1 replies (AMA answers), in page order.
// Every item carries a permalink so claims can be cited to the exact post or comment.
(() => {
  const p = document.querySelector("shreddit-post");
  if (!p) return null;
  const attr = (n, k) => n.getAttribute(k);
  const abs = u => (u ? new URL(u, location.origin).href : null);
  const own = (c, slot) => [...c.querySelectorAll(`[slot="${slot}"]`)].find(s => s.closest("shreddit-comment") === c);
  // Badges next to the username ("Top 1% Commenter", "MOD", "OP", flair text), minus name and age.
  const badges = (meta, author) => (meta?.innerText || "").replace(/\s+/g, " ")
    .replace(author || "", "").replace(/[•·]\s*(\d+\s*\w+\s*ago|edited.*)/gi, "").replace(/^[\s•·]+|[\s•·]+$/g, "").trim() || null;
  const host = attr(p, "author");
  const comments = [...document.querySelectorAll("shreddit-comment")]
    .filter(c => attr(c, "depth") === "0" || (attr(c, "depth") === "1" && attr(c, "author") === host))
    .map(c => {
      let ai = []; try { ai = JSON.parse(attr(c, "ai-declarations") || "[]"); } catch (e) {}
      return {
        author: attr(c, "author"), score: +attr(c, "score"), depth: +attr(c, "depth"),
        created: attr(c, "created"), link: abs(attr(c, "permalink")),
        badges: badges(own(c, "commentMeta"), attr(c, "author")),
        ai_declared: ai.length > 0,
        text: own(c, "comment")?.innerText.trim() ?? "",
      };
    });
  // Moderators listed in the community sidebar (with any flair), to spot brand-run communities.
  const modHead = [...document.querySelectorAll("h2,h3,div,span")].find(e => e.childElementCount === 0 && e.textContent.trim().toUpperCase() === "MODERATORS");
  let modBox = modHead;
  for (let i = 0; i < 6 && modBox && !modBox.querySelector("a[href*='/user/']"); i++) modBox = modBox.parentElement;
  const moderators = modBox ? [...modBox.querySelectorAll("a[href*='/user/']")].map(a => {
    const name = a.getAttribute("href").split("/user/")[1].replace(/\/$/, "");
    const row = (a.closest("li, div[class*=flex]") || a.parentElement).innerText.replace(/\s+/g, " ").trim();
    return { name, flair: row.replace("u/" + name, "").trim() || null };
  }).filter((m, i, arr) => arr.findIndex(x => x.name === m.name) === i).slice(0, 25) : [];
  return {
    title: attr(p, "post-title"), author: host, score: +attr(p, "score"),
    upvote_ratio: attr(p, "upvote-ratio") ? +(+attr(p, "upvote-ratio")).toFixed(2) : null,
    comments_total: +attr(p, "comment-count"), created: attr(p, "created-timestamp"),
    subreddit: attr(p, "subreddit-prefixed-name"),
    url: "https://www.reddit.com" + attr(p, "permalink"),
    flair: p.querySelector("shreddit-post-flair")?.innerText.trim() || null,
    author_badges: badges(p.querySelector("[slot=credit-bar]"), "u/" + host)?.replace(/^.*?ago\s*/, "").replace(/Go to \S+ r\/\S+\s*/, "").trim() || null,
    linked_url: attr(p, "content-href") && !attr(p, "content-href").includes("reddit.com") ? attr(p, "content-href") : null,
    body: p.querySelector('[slot="text-body"]')?.innerText.trim() ?? "",
    moderators,
    comments,
  };
})()
