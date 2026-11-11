// Click the toolbar icon on a tab to start capturing its audio; click again to stop.

async function isActive() {
  const ctx = await chrome.runtime.getContexts({ contextTypes: ["OFFSCREEN_DOCUMENT"] });
  return ctx.length > 0;
}

async function stop() {
  const { tabId } = await chrome.storage.session.get("tabId");
  if (await isActive()) await chrome.offscreen.closeDocument(); // ends the capture
  await chrome.action.setBadgeText({ text: "" });
  if (tabId != null) {
    chrome.tabs.sendMessage(tabId, { type: "caption", kind: "stopped" }).catch(() => {});
  }
  await chrome.storage.session.remove("tabId");
}

chrome.action.onClicked.addListener(async (tab) => {
  const { tabId } = await chrome.storage.session.get("tabId");
  if (await isActive()) {
    await stop();
    if (tabId === tab.id) return; // toggled off
  }
  try {
    const streamId = await chrome.tabCapture.getMediaStreamId({ targetTabId: tab.id });
    await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ["overlay.js"] });
    await chrome.offscreen.createDocument({
      url: `offscreen.html?streamId=${encodeURIComponent(streamId)}&tabId=${tab.id}`,
      reasons: ["USER_MEDIA"],
      justification: "Capture tab audio for live translation",
    });
    await chrome.storage.session.set({ tabId: tab.id });
    await chrome.action.setBadgeBackgroundColor({ color: "#d93025" });
    await chrome.action.setBadgeText({ text: "ON" });
  } catch (e) {
    console.error(e);
    await chrome.action.setBadgeText({ text: "ERR" });
  }
});

// Relay captions from the offscreen document to the page overlay.
chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === "caption" && msg.tabId != null) {
    chrome.tabs.sendMessage(msg.tabId, msg).catch(() => {});
  }
});

chrome.tabs.onRemoved.addListener(async (id) => {
  const { tabId } = await chrome.storage.session.get("tabId");
  if (id === tabId) stop();
});

