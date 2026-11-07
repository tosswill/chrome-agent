// Post metadata and every rendered comment, in page order (read-only). reddit_tab.py picks which
// comments to return (top-level, the OP's replies, optional replies from anyone) and skips
// deleted/empty ones. Every item carries a permalink so claims can be cited to the exact post or
// comment, and its page position (`top`, `bottom`, document px) so the helper knows which comments
// were actually scrolled into view.
(() => {
  const p = document.querySelector("shreddit-post");
  if (!p) {
    const t = (document.body?.innerText || "").slice(0, 4000);
    const m = t.match(/(this post (?:was|has been) (?:removed|deleted)[^\n]*|sorry, this post (?:was|has been) (?:removed|deleted)[^\n]*|post (?:is|was) (?:removed|deleted)[^\n]*|page not found|this community is private|banned)/i);
    return { missing: true, url: location.href, page_says: m ? m[1].trim().slice(0, 160) : null };
  }
  const attr = (n, k) => n.getAttribute(k);
  const abs = u => (u ? new URL(u, location.origin).href : null);
  const own = (c, sel) => [...c.querySelectorAll(sel)].find(s => s.closest("shreddit-comment") === c);
  const txt = e => (e ? (e.innerText || e.shadowRoot?.textContent || "").replace(/\s+/g, " ").trim() : "");
  const sy = scrollY;
  const pos = e => { const r = e.getBoundingClientRect(); return { top: Math.round(r.top + sy), bottom: Math.round(r.bottom + sy) }; };
  // Achievement badges ("Top 1% Commenter") as a list; user flair (often owned gear) separately.
  const badgeList = (el) => {
    if (!el) return [];
    const raw = (el.innerText || el.shadowRoot?.textContent || "").split(/\n|•|·/).map(s => s.trim()).filter(Boolean);
    return [...new Set(raw)];
  };
  const flairOf = (scope) => {
    const f = scope && [...scope.querySelectorAll("author-flair-event-handler, shreddit-post-author-flair, [id^='post-author-flair']")]
      .find(e => !e.hasAttribute("hidden") && txt(e));
    if (!f) return null;
    const lab = f.querySelector("[aria-label^='Flair:']")?.getAttribute("aria-label")?.replace(/^Flair:\s*/, "");
    return (lab || txt(f)).trim() || null;
  };
  const extLinks = (scope) => scope ? [...new Set([...scope.querySelectorAll("a[href]")].map(a => a.href)
    .filter(h => /^https?:/.test(h) && !/^https?:\/\/([a-z]+\.)?reddit\.com\/(user|u|r)\//.test(h)))].slice(0, 30) : [];
  const host = attr(p, "author");
  const comments = [...document.querySelectorAll("shreddit-comment")].map(c => {
    let ai = []; try { ai = JSON.parse(attr(c, "ai-declarations") || "[]"); } catch (e) {}
    const meta = own(c, '[slot="commentMeta"]');
    const body = own(c, '[slot="comment"]');
    const icon = own(c, "shreddit-comment-author-modifier-icon");
    const roles = icon ? ["op", "mod", "moderator", "admin", "staff"].filter(k => icon.hasAttribute(k)) : [];
    const details = [...c.children].find(e => e.tagName === "DETAILS") || c.querySelector("details");
    const parent = c.parentElement?.closest("shreddit-comment");
    const author = attr(c, "author");
    return {
      id: (attr(c, "thingid") || "").replace(/^t1_/, "") || null,
      parent: parent ? (attr(parent, "thingid") || "").replace(/^t1_/, "") : null,
      author, score: +attr(c, "score") || 0, depth: +attr(c, "depth") || 0,
      created: attr(c, "created"), link: abs(attr(c, "permalink")),
      flair: flairOf(meta),
      badges: badgeList(own(c, "shreddit-comment-badges")),
      roles,
      ai_declared: ai.length > 0,
      deleted: author === "[deleted]" || c.hasAttribute("is-author-deleted") || c.hasAttribute("is-comment-deleted"),
      collapsed: !!details && !details.hasAttribute("open"),
      stickied: c.hasAttribute("stickied") || c.hasAttribute("is-stickied"),
      text: body ? body.innerText.trim() : "",
      links: extLinks(body),
      ...pos(c),
    };
  });
  // Moderators listed in the community sidebar (with any flair). The sidebar is only rendered in
  // wide windows; `sidebar` says whether it was there. reddit_tab.py cleans the list.
  const modHead = [...document.querySelectorAll("h2,h3,h4,div,span,summary,faceplate-tracker")].find(e => e.childElementCount === 0 && e.textContent.trim().toUpperCase() === "MODERATORS");
  let modBox = modHead;
  for (let i = 0; i < 8 && modBox && !modBox.querySelector("a[href*='/user/']"); i++) modBox = modBox.parentElement;
  if (modBox && !modBox.querySelector("a[href*='/user/']")) modBox = null;
  // Some communities render the list in a lazily loaded widget instead of under a plain heading.
  if (!modBox) modBox = [...document.querySelectorAll("[id*='moderator' i], [data-testid*='moderator' i], [noun*='moderator' i], faceplate-partial[src*='moderator' i]")]
    .find(e => e.querySelector("a[href*='/user/']")) || null;
  const modPending = !!document.querySelector("faceplate-partial[src*='moderator' i]:not([loaded]), #right-sidebar-container faceplate-partial[src*='right-rail' i]:not([loaded])");
  // The community sidebar's own text (rules, disclaimers), for affiliate/deals communities.
  const side = document.querySelector("#right-sidebar-container, aside");
  const sidebarText = side ? (side.innerText || "").replace(/\s+/g, " ").trim().slice(0, 3000) : "";
  const moderators = modBox ? [...modBox.querySelectorAll("a[href*='/user/']")].map(a => {
    const name = (a.getAttribute("href").split("/user/")[1] || "").split(/[/?#]/)[0];
    const row = (a.closest("li, div[class*=flex]") || a.parentElement).innerText.replace(/\s+/g, " ").trim();
    return { name, flair: row.replace("u/" + name, "").trim() || null };
  }) : [];
  const truncated = !!modBox && /view all moderators/i.test(modBox.innerText || "") ||
    [...document.querySelectorAll("a, button")].some(e => /view all moderators/i.test(e.textContent || ""));
  // Lock/archive banners and the post's own chrome (not comments or the post body).
  const bodyText = p.querySelector('[slot="text-body"]')?.innerText || "";
  const chrome = (p.innerText || "").replace(bodyText, " ");
  const banners = [...document.querySelectorAll("faceplate-banner, shreddit-locked-banner, shreddit-archived-banner, [class*='banner' i], [role='status']")]
    .filter(e => !e.closest("shreddit-comment")).map(e => e.innerText || "").join(" ");
  const head = (chrome + " " + banners).slice(0, 6000);
  // Every post carries a lock icon, hidden unless the post is locked: count it only when shown.
  const lockIcon = [...p.querySelectorAll("[icon-name='lock-fill'], [icon-name='lock-outline'], icon-lock")]
    .some(e => !e.classList.contains("hidden") && !e.closest(".hidden, [hidden]") && e.getBoundingClientRect().width > 0);
  const locked = p.hasAttribute("is-locked") || p.hasAttribute("locked") || lockIcon ||
    /\b(locked post|comments (?:are|have been) locked|this (?:post|thread) (?:is|has been) locked|new comments cannot be posted)\b/i.test(head);
  const archived = p.hasAttribute("archived") || p.hasAttribute("is-archived") ||
    /\b(archived post|this (?:post|thread) (?:is|has been) archived|new comments cannot be posted and votes cannot be cast)\b/i.test(head);
  const credit = p.querySelector("[slot=credit-bar]");
  const affiliate = p.querySelector("shreddit-brand-affiliate-tag");
  const postBody = p.querySelector('[slot="text-body"]');
  return {
    id: (attr(p, "id") || "").replace(/^t3_/, ""),
    title: attr(p, "post-title"), author: host, score: +attr(p, "score") || 0,
    upvote_ratio: attr(p, "upvote-ratio") ? +(+attr(p, "upvote-ratio")).toFixed(2) : null,
    comments_total: +attr(p, "comment-count") || 0, created: attr(p, "created-timestamp"),
    subreddit: attr(p, "subreddit-prefixed-name"),
    url: "https://www.reddit.com" + attr(p, "permalink"),
    flair: p.querySelector("shreddit-post-flair")?.innerText.trim() || null,
    author_flair: flairOf(credit),
    author_badges: badgeList(credit?.querySelector("shreddit-distinguished-post-tags")),
    brand_affiliate_label: affiliate ? (txt(affiliate) || null) : null,
    linked_url: attr(p, "content-href") && !attr(p, "content-href").includes("reddit.com") ? attr(p, "content-href") : null,
    body: postBody?.innerText.trim() ?? "",
    links: extLinks(postBody),
    locked, archived,
    moderators, moderators_truncated: truncated, moderators_heading: !!modHead, moderators_pending: modPending, sidebar_text: sidebarText, sidebar: !!modHead || [...document.querySelectorAll("#right-sidebar-container, aside")].some(e => e.getBoundingClientRect().width > 0),
    post_bottom: pos(p).bottom,
    doc_height: document.documentElement.scrollHeight, view_height: innerHeight, scroll_y: Math.round(sy),
    comments,
  };
})()
