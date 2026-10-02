// Child actor for Dark Reader's own extension pages: forwards a message from
// the page, which Dark Reader accepts as a trusted UI sender.
export class DRSyncChild extends JSWindowActorChild {
  receiveMessage(msg) {
    if (msg.name !== "dr-set") {
      return undefined;
    }
    const win = this.contentWindow;
    const page = win.wrappedJSObject;
    const api = page.browser?.runtime ? page.browser : page.chrome;
    if (!api?.runtime) {
      // Surfaced in OmarchyDR's `debug.last`: tells whether the page loaded
      // and whether the extension APIs were injected into it.
      return "no-api " + JSON.stringify({
        href: win.location.href,
        readyState: win.document.readyState,
        chrome: typeof page.chrome,
        browser: typeof page.browser,
      });
    }
    try {
      api.runtime.sendMessage(msg.data);
      return "ok";
    } catch (e) {
      return "error " + e;
    }
  }
}
