// Where the tab is and whether Reddit is signed in. reddit_tab.py adds the shared wall check
// (research_core.block_check_js) with Reddit's own wall wording: a visible challenge frame always
// counts; wall wording counts only on a page that also looks like a wall, so a post that says
// "unusual activity" no longer stops a run (seen 2026-11-18). Reddit embeds an invisible reCAPTCHA
// anchor on every page, which the shared check ignores because it isn't visible.
(() => {
  return {
    url: location.href,
    title: document.title,
    // true: the account menu is there; false: a log-in button is there instead; null: the header
    // hasn't rendered either yet (slow or throttled page), which is not evidence of being signed out.
    loggedIn: document.querySelector("#expand-user-drawer-button") ? true
      : document.querySelector("#login-button") ? false : null,
  };
})()
