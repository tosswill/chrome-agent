// Where the tab is, whether YouTube is signed in, and whether a consent or age gate is showing.
// youtube_tab.py adds the shared wall check (research_core.block_check_js) with YouTube's own bot-check
// wording, which counts only on a page that also looks like a wall (not in a title or comment).
(() => {
  const text = ((document.title || "") + " " + (document.body?.innerText || "").slice(0, 4000)).toLowerCase();
  return {
    url: location.href,
    title: document.title.replace(/^\u{1F916} \S+ — /u, ""),
    // The avatar isn't rendered in a hidden (background or occluded) window; ytcfg still knows.
    loggedIn: !!document.querySelector("#avatar-btn, button#avatar-btn") || window.ytcfg?.get?.("LOGGED_IN") === true,
    // YouTube's bare 404 page ("This page isn't available. Sorry about that.", title "404 Not Found") is served
    // outside the app shell, so it has no avatar and would read as signed out (wrong channel handle, 2026-11-24).
    notFound: !document.querySelector("ytd-app")
      && (/^404 not found/i.test(document.title) || /this page isn.t available\. sorry about that/.test(text)),
    gate: /consent\.youtube\.com|consent\.google/.test(location.href)
      || /sign in to confirm your age|this video may be inappropriate for some users/.test(text),
  };
})()
