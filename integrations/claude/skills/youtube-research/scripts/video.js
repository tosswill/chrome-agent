// Metadata, description, disclosure signals, chapters and captions of the loaded watch page.
(() => {
  const pr = window.ytInitialPlayerResponse || {};
  const vd = pr.videoDetails || {};
  const mf = pr.microformat?.playerMicroformatRenderer || {};
  const desc = vd.shortDescription || "";
  const links = [...new Set(desc.match(/https?:\/\/[^\s)]+/g) || [])];
  const affiliate = links.filter(l => /amzn\.to|amazon\.[a-z.]+\/.*(tag=|ref=)|geni\.us|bit\.ly|howl\.me|shareasale|impact\.com|go\.skimresources|[?&](tag|ref|aff|affiliate|utm_[a-z]+)=/i.test(l));
  const paidText = document.querySelector(".ytp-paid-content-overlay-text")?.innerText.trim() || "";
  const chapters = [...document.querySelectorAll("ytd-macro-markers-list-item-renderer")].map(c => {
    const t = c.innerText.replace(/\s+/g, " ").trim();
    const m = t.match(/^(.*?)\s+(\d{1,2}(?::\d{2}){1,2})(?:\s|$)/);
    if (!m) return { title: t, at: null };
    let title = m[1];
    const half = title.length / 2;  // the title is often rendered twice ("X X")
    if (Number.isInteger(half - 0.5) && title.slice(0, half - 0.5) === title.slice(half + 0.5)) title = title.slice(0, half - 0.5);
    return { title, at: m[2] };
  });
  const seenCh = new Set();
  return {
    video_id: vd.videoId, title: vd.title, url: vd.videoId ? `https://www.youtube.com/watch?v=${vd.videoId}` : location.href,
    channel: vd.author, channel_url: mf.ownerProfileUrl || (vd.channelId ? `https://www.youtube.com/channel/${vd.channelId}` : null),
    subscribers: document.querySelector("#owner-sub-count")?.textContent.trim() || null,
    views: vd.viewCount ? +vd.viewCount : null,
    likes: document.querySelector("like-button-view-model button, #segmented-like-button button")?.getAttribute("aria-label")?.match(/[\d,.]+[KMB]?/)?.[0] ?? null,
    published: mf.publishDate || null, length_s: vd.lengthSeconds ? +vd.lengthSeconds : null,
    category: mf.category || null, is_live: !!vd.isLiveContent, keywords: (vd.keywords || []).slice(0, 15),
    captions: (pr.captions?.playerCaptionsTracklistRenderer?.captionTracks || []).map(c => c.languageCode + (c.kind === "asr" ? " (auto)" : "")),
    chapters: chapters.filter(c => !seenCh.has(c.title + c.at) && seenCh.add(c.title + c.at)),
    disclosure: { paid_promotion_label: !!paidText, paid_promotion_text: paidText || null,
                  affiliate_links: affiliate },
    description: desc,
    description_links: links,
  };
})()
