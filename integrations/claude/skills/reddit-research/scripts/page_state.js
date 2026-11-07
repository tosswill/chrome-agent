// Where the tab is, whether Reddit is signed in, and whether it is showing a block/CAPTCHA.
// Reddit embeds an invisible reCAPTCHA anchor on every page; only a visible challenge
// frame (or block wording in the page text) counts as blocked.
(() => {
  const t = (document.title || "").toLowerCase();
  const b = (document.body?.innerText || "").slice(0, 3000).toLowerCase();
  const visible = f => {
    const r = f.getBoundingClientRect(), s = getComputedStyle(f);
    return r.width > 100 && r.height > 100 && s.visibility !== "hidden" && s.display !== "none" && +s.opacity > 0;
  };
  const challenge = [...document.querySelectorAll("iframe")].some(f =>
    /recaptcha\/.*\/bframe|hcaptcha|challenges\.cloudflare/.test(f.src || "") && visible(f));
  return {
    url: location.href,
    title: document.title,
    // true: the account menu is there; false: a log-in button is there instead; null: the header
    // hasn't rendered either yet (slow or throttled page), which is not evidence of being signed out.
    loggedIn: document.querySelector("#expand-user-drawer-button") ? true
      : document.querySelector("#login-button") ? false : null,
    blocked: challenge || /whoa there|blocked by network security|unusual activity|too many requests|prove you're human/.test(t + " " + b),
  };
})()
