// Questions and their opened answers on a /p/questions/... page (or the product page's Q&A section).
(() => {
  const clean = s => (s || "").replace(/​/g, "").replace(/\s+/g, " ").trim();
  const out = [];
  for (const qt of document.querySelectorAll("[data-testid=question-title]")) {
    let item = qt;
    for (let i = 0; i < 4 && item; i++) { item = item.parentElement; if (item && /\bBy/.test(item.innerText) && item.innerText.length > clean(qt.innerText).length + 8) break; }
    const meta = clean(item?.innerText.split("\n").find(l => /^By/.test(l.trim())));
    const m = meta.match(/^By\s*(.*?),\s*([A-Z][a-z]{2} \d{1,2}, \d{4})/) || [];
    let wrap = item;
    for (let i = 0; i < 4 && wrap; i++) { wrap = wrap.parentElement; if (wrap && wrap.querySelector("[data-testid=answer-container], [role=region]")) break; }
    const region = wrap ? wrap.querySelector("[role=region]") : null;
    const answers = [];
    for (const at of (region ? region.querySelectorAll("[data-testid=answer-text]") : [])) {
      const box = at.parentElement;
      const by = clean(box.querySelector("[data-testid=username-link]")?.innerText) || null;
      const date = (box.innerText.match(/([A-Z][a-z]{2} \d{1,2}, \d{4})/) || [])[1] || null;
      const badges = [...box.querySelectorAll("ul li")].map(li => clean(li.innerText)).filter(Boolean);
      const text = clean(at.innerText);
      answers.push({by, date, badges, text: text.slice(0, 1500),
                    other_variant: (text.match(/This question is from (.+?)\.?$/) || [])[1] || null});
    }
    const count = parseInt(((item?.innerText || "").match(/(\d+) Answers?/) || [])[1]) || 0;
    out.push({question: clean(qt.innerText), asker: m[1] || null, date: m[2] || null, answer_count: count, answers});
  }
  const v = document.body.innerText.match(/Viewing (\d+)-(\d+) of ([\d,]+)/) || [];
  return {questions: out, viewing: v[0] || null, total: v[3] ? parseInt(v[3].replace(/,/g, "")) : null};
})()
