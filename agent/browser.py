"""Real browser control via Playwright.

The agent "sees" a page as a compact, indexed list of interactive elements plus the visible text
(an accessibility-tree-like view). This is cheaper, faster and far more deterministic than
pixel-based clicking; screenshots are still taken after every action as human-facing evidence.
"""
import os
from pathlib import Path

from playwright.sync_api import sync_playwright, Error as PWError

SNAPSHOT_JS = r"""
() => {
  document.querySelectorAll('[data-oa]').forEach(e => e.removeAttribute('data-oa'));
  const els = [...document.querySelectorAll('a,button,input,select,textarea,[onclick],[role=button]')];
  const out = []; let i = 0;
  for (const el of els) {
    const r = el.getBoundingClientRect(); const st = getComputedStyle(el);
    if (r.width === 0 || r.height === 0 || st.visibility === 'hidden' || st.display === 'none') continue;
    if (el.type === 'hidden') continue;
    i++; el.setAttribute('data-oa', i);
    const tag = el.tagName.toLowerCase();
    let d = `[${i}] ${tag}`;
    if (tag === 'input') d += ` type=${el.type}`;
    if (el.name) d += ` name="${el.name}"`;
    const lab = el.labels && el.labels[0] ? el.labels[0].innerText.split('\n')[0].trim() : '';
    if (lab) d += ` label="${lab}"`;
    const txt = (el.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 80);
    if (txt && tag !== 'select' && tag !== 'textarea') d += ` "${txt}"`;
    if (el.placeholder) d += ` placeholder="${el.placeholder}"`;
    if (tag === 'a' && el.getAttribute('href')) d += ` href="${el.getAttribute('href')}"`;
    if (tag === 'input' || tag === 'textarea') d += ` value="${el.type === 'password' && el.value ? '***' : el.value}"`;
    if (tag === 'select') d += ` selected="${el.options[el.selectedIndex]?.text || ''}" options=[${[...el.options].map(o => o.text).join(' | ')}]`;
    out.push(d);
  }
  const text = document.body ? document.body.innerText.replace(/\n\s*\n+/g, '\n').trim().slice(0, 4000) : '';
  return {elements: out, text};
}
"""

FORM_VALUES_JS = r"""
(el) => {
  const f = el.form || el.closest('form');
  if (!f) return {};
  const o = {};
  for (const [k, v] of new FormData(f).entries()) o[k] = String(v);
  return o;
}
"""


class BrowserError(Exception):
    pass


class Browser:
    def __init__(self, shots_dir: Path, headless: bool = False, slow_mo: int = 200):
        self.shots_dir = shots_dir
        self.headless, self.slow_mo = headless, slow_mo
        self._pw = self._browser = self.page = None
        self._shot_n = 0

    def start(self):
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=self.headless, slow_mo=self.slow_mo)
        opts = {"viewport": {"width": 1280, "height": 800}}
        if os.getenv("RECORD_VIDEO") == "1":  # screen-record the browser session as evidence
            opts.update(record_video_dir=str(self.shots_dir.parent / "video"), record_video_size={"width": 1280, "height": 800})
        self._ctx = self._browser.new_context(**opts)
        self.page = self._ctx.new_page()
        self.page.on("dialog", lambda d: d.accept())
        return self

    def close(self):
        try:
            self._ctx.close()  # flushes the video file
            self._browser.close()
            self._pw.stop()
        except Exception:
            pass

    # ------------------------------------------------------------------ observation
    def snapshot(self) -> str:
        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=5000)
            data = self.page.evaluate(SNAPSHOT_JS)
        except PWError as e:
            return f"(could not read page: {e.message.splitlines()[0]})"
        els = "\n".join(data["elements"]) or "(no interactive elements)"
        return (f"URL: {self.page.url}\nTITLE: {self.page.title()}\n"
                f"INTERACTIVE ELEMENTS:\n{els}\nVISIBLE TEXT:\n{data['text']}")

    def screenshot(self, label: str) -> str:
        self._shot_n += 1
        name = f"{self._shot_n:03d}_{label}.png"
        try:
            self.page.screenshot(path=str(self.shots_dir / name))
        except PWError:
            return ""
        return name

    # ------------------------------------------------------------------ actions
    def _el(self, element_id: int):
        loc = self.page.locator(f'[data-oa="{int(element_id)}"]')
        if loc.count() == 0:
            raise BrowserError(f"Element [{element_id}] does not exist on the current page "
                               "(the page may have changed). Use read_page to get fresh element ids.")
        return loc.first

    def describe(self, element_id: int) -> str:
        el = self._el(element_id)
        return (el.inner_text(timeout=2000) or el.get_attribute("value") or "").strip()

    def form_values(self, element_id: int) -> dict:
        return self._el(element_id).evaluate(FORM_VALUES_JS)

    def navigate(self, url: str) -> str:
        try:
            resp = self.page.goto(url, wait_until="domcontentloaded", timeout=15000)
        except PWError as e:
            raise BrowserError(f"Navigation failed: {e.message.splitlines()[0]}")
        return f"HTTP {resp.status if resp else '?'}"

    def click(self, element_id: int) -> str:
        el = self._el(element_id)
        try:
            el.click(timeout=4000)
            self.page.wait_for_load_state("domcontentloaded", timeout=10000)
        except PWError as e:
            msg = e.message.splitlines()
            hint = next((l.strip() for l in msg if "intercepts pointer events" in l), "")
            raise BrowserError(f"Click on [{element_id}] failed: {msg[0]}"
                               + (f" | Something is covering the element: {hint}" if hint else ""))
        return "clicked"

    def type_text(self, element_id: int, text: str) -> str:
        try:
            self._el(element_id).fill(str(text), timeout=4000)
        except PWError as e:
            raise BrowserError(f"Typing into [{element_id}] failed: {e.message.splitlines()[0]}")
        return f"typed {text!r}"

    def select(self, element_id: int, option: str) -> str:
        try:
            self._el(element_id).select_option(label=str(option), timeout=4000)
        except PWError as e:
            raise BrowserError(f"Selecting {option!r} in [{element_id}] failed: {e.message.splitlines()[0]}")
        return f"selected {option!r}"
