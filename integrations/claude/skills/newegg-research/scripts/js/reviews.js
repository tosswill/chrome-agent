// The open Reviews tab: cards with verified-owner status, ownership time, related variant, pros/cons/overall.
(() => {
  const clean = s => (s || "").replace(/​/g, "").replace(/[ \t]+/g, " ").trim();
  const out = [];
  for (const c of document.querySelectorAll(".comments-cell")) {
    if (c.parentElement.closest(".comments-cell")) continue;  // a manufacturer reply nested in a review
    const t = c.innerText || "";
    const rc = (c.querySelector(".comments-title i.rating, i.rating")?.className.match(/rating-(\d)(?:-(\d))?/) || []);
    const part = label => { const p = [...c.querySelectorAll("p")].find(p => p.querySelector("strong")?.innerText.trim().startsWith(label));
      return p ? clean(p.innerText.replace(/^[^:]*:/, "")) : null; };
    const rel = [...c.querySelectorAll(".comments-related-spec")].map(e => clean(e.innerText)).join("; ");
    const mfr = [...c.querySelectorAll("*")].find(e => /Manufacturer Response/i.test(e.textContent) && e.children.length < 3);
    out.push({
      author: clean(c.querySelector(".comments-name")?.innerText) || null,
      verified_owner: !!c.querySelector(".comments-verified-owner") || /Verified Owner/.test(t),
      ownership: (t.match(/Ownership:\s*([^\n]+)/) || [])[1] || null,
      stars: rc[1] ? parseFloat(rc[1] + (rc[2] ? "." + rc[2] : "")) : null,
      title: clean(c.querySelector(".comments-title-content")?.innerText) || null,
      date: (t.match(/(\d{1,2}\/\d{1,2}\/\d{4})/) || [])[1] || null,
      related_product: rel || null,
      related_url: c.querySelector(".comments-related-view a")?.href || null,
      pros: part("Pros"), cons: part("Cons"), overall: part("Overall Review"),
      helpful: parseInt(clean(c.querySelector("button[title=like]")?.innerText)) || 0,
      incentivized: /incentiviz|received (?:this|the) product (?:for free|free)|free (?:sample|product) in exchange/i.test(t),
      manufacturer_response: mfr ? clean(mfr.parentElement.innerText).slice(0, 600) : null,
    });
  }
  const pane = [...document.querySelectorAll(".tab-pane")].find(e => /out of 5 eggs/.test(e.innerText || ""));
  const sh = ((pane && pane.innerText) || "").match(/show (?:(\d+)-(\d+)|\d+) of ([\d,]+) reviews/) || [];
  return {reviews: out, showing: sh[0] || null, total: sh[3] ? parseInt(sh[3].replace(/,/g, "")) : null,
          sort: pane?.querySelector("select")?.selectedOptions?.[0]?.text || null};
})()
