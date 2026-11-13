// Product cards on a Newegg search or category page (div.goods-container with a price; compare widgets skipped).
(() => {
  const clean = s => (s || "").replace(/​/g, "").replace(/\s+/g, " ").trim();
  const out = [], seen = new Set();
  for (const c of document.querySelectorAll("div.goods-container[data-itemnumber], .item-container")) {
    const a = c.querySelector("a.goods-title, a.item-title");
    if (!a || !c.querySelector(".goods-price, .price")) continue;
    const id = c.getAttribute("data-itemnumber") || (a.href.match(/Item=([\w-]+)|\/p\/([\w-]+)/) || []).slice(1).find(Boolean);
    if (seen.has(id)) continue;
    seen.add(id);
    const t = c.innerText || "";
    const rating = c.querySelector("[aria-label*='out of 5']")?.getAttribute("aria-label") || c.querySelector("[title^='Rating']")?.title || "";
    const cur = clean(c.querySelector(".goods-price-current, .price-current")?.textContent).match(/\$\s?[\d,]+(?:\.\d{2})?/);
    const was = clean(c.querySelector(".goods-price-was, .price-was-data")?.textContent).match(/\$\s?[\d,]+(?:\.\d{2})?/);
    if (c.closest(".swiper-slide")) continue;  // the brand carousel above the results
    out.push({
      item: id, url: a.href.split("?")[0], title: clean(a.innerText) || a.title || null,
      brand: c.querySelector("a.goods-brand img, .item-brand img")?.getAttribute("title") || c.querySelector("a.goods-brand img")?.alt || null,
      price: cur ? cur[0].replace(/\s/g, "") : null, was_price: was ? was[0] : null,
      rating: parseFloat((rating.match(/[\d.]+/) || [])[0]) || null,
      reviews: parseInt((clean(c.querySelector(".goods-rating-num, .item-rating-num")?.textContent).match(/[\d,]+/) || [""])[0].replace(/,/g, "")) || null,
      refurbished: /\bREFURBISHED\b|open box/i.test(clean(c.querySelector(".item-open-box-italic")?.textContent) || t.slice(0, 200)),
      ships_from: (t.match(/\bfrom ([A-Z][A-Za-z .]+?)\s*(?:\n|$)/) || [])[1] || null,
      direct_from: (t.match(/Available Direct from ([^\n]+)/) || [])[1] || null,
      promo: clean(c.querySelector(".goods-promo")?.innerText) || null,
    });
  }
  return out;
})()
