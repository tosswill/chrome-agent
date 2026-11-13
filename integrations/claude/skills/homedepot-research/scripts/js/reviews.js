// Review cards in the open reviews list (product page section or /p/reviews/... page).
(() => {
  const clean = s => (s || "").replace(/​/g, "").replace(/\s+/g, " ").trim();
  const out = [];
  for (const d of document.querySelectorAll(".review-content__date")) {
    let card = d;
    for (let i = 0; i < 8 && card; i++) { card = card.parentElement; if (card && card.querySelector(".helpful__wrapper, .report-review__text")) break; }
    if (!card) continue;
    const t = card.innerText || "";
    const body = clean(card.querySelector(".review-content-body")?.innerText);
    const stars = card.querySelector("[name=simple-rating], [aria-label$=Stars]")?.getAttribute("aria-label") || "";
    const syn = (t.match(/Customer review from ([\w.-]+\.\w+)/) || [])[1];
    const variant = (t.match(/This review is from\s+([^\n]+)/) || [])[1];
    const helpful = (t.match(/(\d+) found this review helpful/) || [])[1];
    const author = clean(card.querySelector(".review-content__no-padding button, .review-content__no-padding")?.innerText).replace(/^by\s+/i, "");
    out.push({
      date: clean(d.textContent), stars: parseFloat((stars.match(/[\d.]+/) || [])[0]) || null,
      title: clean(card.querySelector(".review-content__title")?.innerText) || null,
      text: body ? body.replace(/^\[This review was collected as part of a promotion\.\]\s*/, "") : null,
      author: author || null,
      verified_purchase: /Verified Purchase/.test(t),
      incentivized: /Incentivized Review Program/.test(t),
      promotion: /collected as part of a promotion/i.test(body || ""),
      syndicated_from: syn || null,
      recommended: /\bRecommended\b/.test(t) ? true : null,
      variant: variant ? clean(variant).slice(0, 160) : null,
      helpful: helpful ? parseInt(helpful) : 0,
      no_text: /chose to rate the product and not provide text/.test(t),
    });
  }
  const showing = (document.body.innerText.match(/Showing (\d+)-(\d+) of ([\d,]+) reviews/) || []);
  return {reviews: out, showing: showing[0] || null,
          total: showing[3] ? parseInt(showing[3].replace(/,/g, "")) : null,
          sort: document.querySelector("select.drop-down__select")?.value || null};
})()
