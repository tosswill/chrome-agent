// The open Q & A tab (TurnTo widget): questions, answers with author, date and badges; AI answers flagged.
(() => {
  const clean = s => (s || "").replace(/​/g, "").replace(/\s+/g, " ").trim();
  const out = [];
  for (const box of document.querySelectorAll(".tt-c-question-container, .tt-c-question__comments")) {
    const q = box.querySelector(".tt-c-question__title");
    if (!q) continue;
    const by = box.querySelector(".tt-c-question .tt-o-byline__author, .tt-c-question__byline-name");
    const badge = clean(box.querySelector(".tt-c-question .tt-o-badge, .tt-c-question__badge")?.innerText) || null;
    const answers = [], seenA = new Set();
    for (const a of box.querySelectorAll(".tt-c-answer, .tt-c-comment")) {
      const author = clean(a.querySelector(".tt-o-byline__author")?.textContent) || null;
      const key = author + "|" + clean(a.querySelector(".tt-c-answer__text, .tt-c-comment__text")?.innerText).replace(/^Best Answer:\s*/i, "");
      if (seenA.has(key)) continue;  // the "best answer" is shown again in the answer list
      seenA.add(key);
      answers.push({
        by: author, date: clean(a.querySelector("time")?.textContent) || null,
        badges: [...a.querySelectorAll(".tt-o-badge")].map(b => clean(b.innerText)).filter(Boolean),
        text: clean(a.querySelector(".tt-c-answer__text, .tt-c-comment__text")?.innerText).replace(/^Best Answer:\s*/i, "").slice(0, 1500),
        best: a.classList.contains("tt-c-answer--best"),
        ai: /AI Assistant/i.test(author || ""),
        kind: a.classList.contains("tt-c-comment") ? "owner comment" : "answer",
      });
    }
    out.push({question: clean(q.innerText), asker: clean(by?.textContent) || null, asker_badge: badge,
              date: clean(box.querySelector(".tt-c-question time")?.textContent) || null, answers});
  }
  const total = parseInt((clean(document.querySelector(".tt-c-questions__total")?.innerText).match(/\d+/) || [])[0]) || null;
  return {questions: out, total};
})()
