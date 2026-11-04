// Post metadata + top-level comments and the OP's depth-1 replies (AMA answers), in page order.
(() => {
  const p = document.querySelector("shreddit-post");
  if (!p) return null;
  const attr = (n, k) => n.getAttribute(k);
  const own = c => [...c.querySelectorAll('[slot="comment"]')].find(s => s.closest("shreddit-comment") === c);
  const host = attr(p, "author");
  const comments = [...document.querySelectorAll("shreddit-comment")]
    .filter(c => attr(c, "depth") === "0" || (attr(c, "depth") === "1" && attr(c, "author") === host))
    .map(c => ({ author: attr(c, "author"), score: +attr(c, "score"), depth: +attr(c, "depth"),
                 text: own(c)?.innerText.trim() ?? "" }));
  return {
    title: attr(p, "post-title"), author: host, score: +attr(p, "score"),
    comments_total: +attr(p, "comment-count"), created: attr(p, "created-timestamp"),
    url: "https://www.reddit.com" + attr(p, "permalink"),
    body: p.querySelector('[slot="text-body"]')?.innerText.trim() ?? "",
    comments,
  };
})()
