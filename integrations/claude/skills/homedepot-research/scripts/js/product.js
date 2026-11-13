// A product page: structured data (JSON-LD), breadcrumbs, details, highlights, specifications (when the
// Specifications section is open), the reviews summary (when Customer Reviews is open) and the Q&A count.
(() => {
  const clean = s => (s || "").replace(/​/g, "").replace(/[ \t]+/g, " ").trim();
  const ld = [...document.querySelectorAll('script[type="application/ld+json"]')].map(s => {
    try { return JSON.parse(s.textContent); } catch (e) { return null; } }).filter(Boolean);
  const p = ld.find(j => j["@type"] === "Product") || {};
  const page = ld.find(j => j["@type"] === "WebPage") || {};
  const crumbs = ((page.breadcrumb || {}).itemListElement || []).map(i => i.name || (i.item || {}).name).filter(Boolean);
  const unavailable = /product you are trying to view is not currently available/i.test(document.body.innerText.slice(0, 3000));
  const overview = document.querySelector("#product-section-overview");
  const otext = overview ? overview.innerText : "";
  const hi = otext.split(/\n/).map(clean);
  const hIdx = hi.indexOf("Highlights");
  const highlights = hIdx >= 0 ? hi.slice(hIdx + 1).filter(x => x && !/^(Specifications|Questions|Customer Reviews)/.test(x)).slice(0, 15) : [];
  const about = (otext.match(/About This Product\s*\n+([\s\S]*?)(?:\n\s*Highlights|$)/) || [])[1];
  const specs = {};
  for (const tr of document.querySelectorAll("#product-section-key-feat tr")) {
    const k = clean(tr.querySelector("th")?.innerText), v = clean(tr.querySelector("td")?.innerText);
    if (k && v) specs[k] = v;
  }
  const rr = document.querySelector("#product-section-rr");
  const rtext = rr ? rr.innerText : "";
  const hist = {};
  const hm = rtext.match(/recommend this product\s*\n+([\s\S]*?)\n+Write a Review/);
  if (hm) {
    const nums = hm[1].split(/\n+/).map(x => x.trim()).filter(x => /^\d[\d,]*$/.test(x));
    for (let i = 0; i + 1 < nums.length; i += 2) hist[nums[i]] = parseInt(nums[i + 1].replace(/,/g, ""));
  }
  const summary = (rtext.match(/Summarized by generative AI from text of customer reviews\s*\n+([^\n]+)/) || [])[1];
  const themes = summary ? (rtext.split(summary)[1] || "").split(/\n+/).map(clean).filter(x => x && x.length < 40 && !/^\d+$/.test(x))
    .slice(0, 12).filter(x => !/^(Customer Images|Filter by:)$/.test(x)) : [];
  const ti = themes.indexOf("Customer Images");
  const qa = (document.querySelector("#product-section-qa")?.innerText.match(/([\d,]+) Questions?/) || [])[1];
  const price = clean(document.querySelector("[data-testid=price]")?.textContent).match(/\$\s?[\d,]+(?:\.\d{2})?/g) || [];
  return {
    unavailable, url: location.href, name: p.name || clean(document.querySelector("h1")?.innerText) || null,
    brand: (p.brand || {}).name || null, model: p.model || null, sku: p.sku || null, gtin: p.gtin13 || null,
    product_id: p.productID || null, price: price[0] || (p.offers && p.offers.price != null ? "$" + p.offers.price : null),
    was_price: price.length > 1 && /save|was/i.test(document.querySelector("[data-testid=price]")?.textContent || "") ? price[1] : null,
    rating: p.aggregateRating ? parseFloat(p.aggregateRating.ratingValue) : null,
    review_count: p.aggregateRating ? p.aggregateRating.reviewCount : null,
    recommend_pct: parseInt((rtext.match(/(\d+)% recommend/) || [])[1]) || null,
    histogram: Object.keys(hist).length ? hist : null,
    ai_summary: summary || null, review_themes: themes.slice(0, ti >= 0 ? ti : 8),
    questions: qa ? parseInt(qa.replace(/,/g, "")) : null,
    breadcrumbs: crumbs, about: about ? clean(about).slice(0, 1500) : null, highlights, specifications: specs,
    dimensions: {depth: p.depth || null, height: p.height || null, width: p.width || null, weight: p.weight || null},
  };
})()
