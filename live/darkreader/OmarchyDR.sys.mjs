// Omarchy -> Dark Reader sync (loaded by omarchy-zen.cfg).
//
// Pushes the active Omarchy palette (--custom-zen-bg/fg/selection-bg and the
// color scheme) into Dark Reader's dynamic theme, live, with no reload.
//
// Dark Reader only accepts "ui-bg-set-theme" from its own UI pages, so we keep
// one hidden <browser> on its options page and ask a small window actor
// (DRSyncChild) living in that page to forward the message.
//
// Everything is best-effort: if Dark Reader is missing or anything throws, we
// retry a few times (it may still be starting) and then give up quietly until
// the next theme change.

const DR_ID = "addon@darkreader.org";
const ACTOR = "DRSync"; // must match the exported class DRSyncChild
const RETRY_MS = 2000;
const MAX_ATTEMPTS = 30;
const COLOR = /^(#[0-9a-f]{3,8}|rgba?\([\d\s.,%/]+\))$/i;

// Last outcome, for diagnostics from the Browser Console.
export const debug = { last: null, attempts: 0 };

let browserEl = null;
let actorRegistered = false;
let pending = null;
let timer = null;
let attempts = 0;

const { setTimeout, clearTimeout } = ChromeUtils.importESModule(
  "resource://gre/modules/Timer.sys.mjs"
);

function readVar(css, name) {
  const m = css.match(new RegExp(`--custom-zen-${name}\\s*:\\s*([^;!}]+)`));
  const value = m && m[1].trim();
  return value && COLOR.test(value) ? value : null;
}

function parse(css) {
  const bg = readVar(css, "bg");
  const fg = readVar(css, "fg");
  if (!bg || !fg) {
    return null;
  }
  const scheme = /--custom-zen-color-scheme\s*:\s*light/.test(css) ? "light" : "dark";
  return { bg, fg, selection: readVar(css, "selection-bg"), scheme };
}

function toMessage({ bg, fg, selection, scheme }) {
  const dark = scheme !== "light";
  const data = {
    mode: dark ? 1 : 0,
    [dark ? "darkSchemeBackgroundColor" : "lightSchemeBackgroundColor"]: bg,
    [dark ? "darkSchemeTextColor" : "lightSchemeTextColor"]: fg,
  };
  if (selection) {
    data.selectionColor = selection;
  }
  return { type: "ui-bg-set-theme", data };
}

function ensureActor() {
  if (actorRegistered) {
    return;
  }
  try {
    ChromeUtils.registerWindowActor(ACTOR, {
      child: { esModuleURI: "resource://omarchy-dr/DRSyncChild.sys.mjs" },
      allFrames: true,
      matches: ["moz-extension://*/*"],
      remoteTypes: ["extension"],
    });
  } catch (e) {
    // Already registered (module reloaded): fine.
  }
  actorRegistered = true;
}

function dropBrowser() {
  try {
    browserEl?.remove();
  } catch (e) {}
  browserEl = null;
}

async function ensureBrowser(policy) {
  if (browserEl?.isConnected && browserEl.browsingContext?.currentWindowGlobal) {
    return browserEl;
  }
  dropBrowser();
  const win = Services.wm.getMostRecentWindow("navigator:browser");
  if (!win?.document?.documentElement) {
    return null;
  }
  const b = win.document.createXULElement("browser");
  b.setAttribute("type", "content");
  b.setAttribute("remote", "true");
  b.setAttribute("remoteType", "extension");
  b.setAttribute("maychangeremoteness", "true");
  b.setAttribute("webextension-view-type", "tab");
  b.setAttribute("messagemanagergroup", "webext-browsers");
  b.setAttribute("style", "width:1px;height:1px;position:fixed;left:-10px;top:-10px;visibility:hidden");
  win.document.documentElement.appendChild(b);
  policy.extension.emit("extension-browser-inserted", b);
  const uri = Services.io.newURI(
    `moz-extension://${policy.mozExtensionHostname}/ui/options/index.html`
  );
  b.loadURI(uri, {
    triggeringPrincipal: Services.scriptSecurityManager.createContentPrincipal(uri, {}),
  });
  browserEl = b;
  // Wait for the page (up to ~6s), then the caller talks to its actor.
  for (let i = 0; i < 30; i++) {
    await new Promise(r => setTimeout(r, 200));
    if (b.browsingContext?.currentWindowGlobal?.documentURI?.spec === uri.spec) {
      return b;
    }
  }
  dropBrowser();
  return null;
}

async function trySend(vars) {
  const policy = WebExtensionPolicy.getByID(DR_ID);
  if (!policy?.active) {
    debug.last = "dark reader inactive/missing";
    return false;
  }
  ensureActor();
  const b = await ensureBrowser(policy);
  if (!b) {
    debug.last = "hidden page not ready";
    return false;
  }
  const actor = b.browsingContext.currentWindowGlobal.getActor(ACTOR);
  const reply = await Promise.race([
    actor.sendQuery("dr-set", toMessage(vars)),
    new Promise(r => setTimeout(() => r("timeout"), 5000)),
  ]);
  if (typeof reply !== "string" || !reply.startsWith("ok")) {
    debug.last = "reply: " + reply;
    dropBrowser();
    return false;
  }
  return true;
}

async function attempt() {
  timer = null;
  const vars = pending;
  if (!vars) {
    return;
  }
  attempts++;
  debug.attempts++;
  let ok = false;
  try {
    ok = await trySend(vars);
    if (ok) {
      debug.last = "ok";
    }
  } catch (e) {
    debug.last = String(e) + " @ " + (e && e.stack);
    dropBrowser();
  }
  if (ok) {
    if (pending === vars) {
      pending = null;
    }
    attempts = 0;
    return;
  }
  if (attempts < MAX_ATTEMPTS && pending) {
    timer = setTimeout(attempt, RETRY_MS);
  }
}

// Called by the cfg each time custom-zen.css changes.
export function sync(css) {
  const vars = parse(css);
  if (!vars) {
    return;
  }
  pending = vars;
  attempts = 0;
  if (timer) {
    clearTimeout(timer);
  }
  timer = setTimeout(attempt, 0);
}
