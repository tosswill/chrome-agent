// A Newegg product page: header, price, seller, bullets, specs (Specs tab content) and the reviews summary.
(() => {
  const clean = s => (s || "").replace(/​/g, "").replace(/\s+/g, " ").trim();
  const body = document.body.innerText;
  const specs = {};
  for (const r of document.querySelectorAll("table tr")) {
    const k = clean(r.querySelector("th")?.innerText), v = clean(r.querySelector("td")?.innerText);
    if (k && v && !(k in specs)) specs[k] = v.slice(0, 200);
  }
  // Price fields come from the buy box only: carousels ("Keep Exploring", "Similar") reuse the same classes.
  const box = document.querySelector(".product-buy-box, .product-pane") || document;
  const prices = [...box.querySelectorAll("[class*=price-current]")].map(e => clean(e.textContent))
    .map(t => (t.match(/\$\s?[\d,]+(?:\.\d{2})?/) || [])[0]).filter(Boolean);
  const pane = [...document.querySelectorAll(".tab-pane")].find(e => /customer ratings/.test(e.innerText || ""));
  const ptext = pane ? pane.innerText : "";
  const pct = (ptext.match(/with reviews\s*\n+([\s\S]*?)\n\s*Customer Images/) || [])[1];
  const hist = {};
  if (pct) pct.split(/\n+/).map(x => x.trim()).filter(x => /^\d+%$/.test(x)).forEach((p, i) => { hist[String(5 - i)] = p; });
  const crumbs = [...document.querySelectorAll(".breadcrumb a, ol.breadcrumb li, .breadcrumb li")].map(e => clean(e.innerText)).filter(Boolean);
  return {
    url: location.href.split("?")[0], title: clean(document.querySelector("h1.product-title, h1")?.innerText) || null,
    item: (location.pathname.match(/\/p\/([\w-]+)/) || [])[1] || null,
    brand: specs["Brand"] || null, model: specs["Model"] || null,
    price: prices[0] ? prices[0].replace(/\s/g, "") : null,
    was_price: clean(box.querySelector(".price-was-data")?.textContent) || null,
    sold_by: clean((body.match(/Sold by\s*:?\s*([^\n]+)/) || [])[1]) || null,
    ships_from: clean((body.match(/Ships from\s*:?\s*([^\n]+)/) || [])[1]) || null,
    rating: parseFloat((ptext.match(/([\d.]+) out of 5 eggs/) || [])[1]) || null,
    ratings_count: parseInt(((ptext.match(/([\d,]+) customer ratings/) || [])[1] || "").replace(/,/g, "")) || null,
    reviews_with_text: parseInt(((ptext.match(/([\d,]+) with reviews/) || [])[1] || "").replace(/,/g, "")) || null,
    histogram: Object.keys(hist).length ? hist : null,
    questions: parseInt((([...document.querySelectorAll(".tab-nav")].map(e => e.innerText).find(t => /^Q ?& ?A/.test(t.trim())) || "").match(/\((\d+)\)/) || [])[1]) || null,
    best_seller_rank: specs["Best Seller Ranking"] || null,
    bullets: [...document.querySelectorAll(".product-bullets li")].map(l => clean(l.innerText)).filter(Boolean).slice(0, 12),
    breadcrumbs: crumbs.slice(0, 10), specifications: specs,
  };
})()
