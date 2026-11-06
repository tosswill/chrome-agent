// Where the tab is, whether YouTube is signed in, and whether a wall is showing.
(() => {
  const text = ((document.title || "") + " " + (document.body?.innerText || "").slice(0, 4000)).toLowerCase();
  const visible = el => { const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 100 && r.height > 100 && s.visibility !== "hidden" && s.display !== "none"; };
  const challenge = [...document.querySelectorAll("iframe")].some(f =>
    /recaptcha\/.*\/bframe|hcaptcha|challenges\.cloudflare/.test(f.src || "") && visible(f));
  return {
    url: location.href,
    title: document.title.replace(/^\u{1F916} \S+ — /u, ""),
    // The avatar isn't rendered in a hidden (background or occluded) window; ytcfg still knows.
    loggedIn: !!document.querySelector("#avatar-btn, button#avatar-btn") || window.ytcfg?.get?.("LOGGED_IN") === true,
    blocked: challenge || /not a bot|unusual traffic|our systems have detected|automated queries/.test(text),
    gate: /consent\.youtube\.com|consent\.google/.test(location.href)
      || /sign in to confirm your age|this video may be inappropriate for some users/.test(text),
  };
})()
