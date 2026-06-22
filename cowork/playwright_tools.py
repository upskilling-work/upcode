"""Browser-testing tool (Playwright) used by the QATester agent.

Playwright is imported lazily inside the tool so the rest of the app does not
require it to be installed. Install with: ``pip install playwright`` and
``playwright install chromium``.
"""

from __future__ import annotations

from .tools import tool

_TIMEOUT_MS = 10_000

# Whether the browser runs headless (no visible window). Default True; toggle it
# off from the UI with `/headless` to watch the test run in a real window. It is
# the effective default used when `browser_test` is called without an explicit
# value.
_headless: bool = True


def set_headless(value: bool) -> None:
    """Set the default headless mode for `browser_test` (UI toggle)."""
    global _headless
    _headless = bool(value)


def headless_enabled() -> bool:
    """Current default headless mode for the browser tool."""
    return _headless


def _run_step(page, step: str) -> tuple[bool, str]:
    """Run one DSL step; returns (ok, info/error)."""
    cmd, _, arg = step.strip().partition(" ")
    cmd, arg = cmd.lower(), arg.strip()
    try:
        if cmd == "goto":
            resp = page.goto(arg, wait_until="load")
            return True, f"HTTP {resp.status if resp else '?'}"
        if cmd == "click":
            page.click(arg)
        elif cmd == "fill":
            sel, _, val = arg.partition(" ")
            page.fill(sel, val.strip())
        elif cmd in ("expect_text", "assert_text"):
            page.get_by_text(arg, exact=False).first.wait_for()
        elif cmd in ("expect_selector", "assert_selector"):
            page.wait_for_selector(arg)
        elif cmd == "wait":
            page.wait_for_timeout(int(arg or "1000"))
        elif cmd == "press":
            sel, _, key = arg.partition(" ")
            if key:
                page.press(sel, key.strip())
            else:
                page.keyboard.press(sel)
        elif cmd == "screenshot":
            page.screenshot(path=arg or "screenshot.png")
            return True, f"saved {arg or 'screenshot.png'}"
        else:
            return False, f"unknown command '{cmd}'"
        return True, ""
    except Exception as exc:  # noqa: BLE001 — becomes FAIL in the report
        return False, str(exc).splitlines()[0][:200]


def _report(url, results, page_errors, failed_requests) -> str:
    ok = sum(1 for _, passed, _ in results if passed)
    fail = len(results) - ok
    lines = [f"Browser test report — {url}", ""]
    for step, passed, info in results:
        tag = "OK  " if passed else "FAIL"
        lines.append(f"[{tag}] {step}" + (f"  ({info})" if info else ""))
    if page_errors:
        lines += ["", "JavaScript/page errors:"]
        lines += [f"  - {e}" for e in dict.fromkeys(page_errors)][:20]
    if failed_requests:
        lines += ["", "Failed requests (HTTP >= 400):"]
        lines += [f"  - {r}" for r in dict.fromkeys(failed_requests)][:20]
    lines += ["", f"Summary: {ok} OK, {fail} FAIL out of {len(results)} step(s)."]
    return "\n".join(lines)


@tool
def browser_test(url: str, steps: list[str], headless: bool | None = None) -> str:
    """Open a website in a real browser (Playwright/Chromium), run navigation
    steps, and return a report of what works and what fails.

    Use to test a live site/page end to end in a real browser.

    Args:
        url: starting site address (http/https).
        steps: ordered list of steps, one per item, in this simple DSL:
            - goto <url>                 navigate to another URL
            - click <selector>           click (CSS, or "text=Some text")
            - fill <selector> <value>    type into a field
            - expect_text <text>         assert the text appears on the page
            - expect_selector <selector> assert the element exists/visible
            - wait <ms>                  wait N milliseconds
            - press <selector> <key>     press a key (e.g. Enter); selector
                                         optional for a global key press
            - screenshot <file.png>      save a screenshot
        headless: run without a visible window. Omitted (the default), it follows
            the UI setting (toggled with `/headless`, on by default so it runs
            without a window). Pass true/false to force it for this run.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return ("Error: Playwright is not installed. Run: "
                "pip install playwright && playwright install chromium")

    # No explicit choice: use the UI default (off = visible window).
    if headless is None:
        headless = headless_enabled()

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    results: list[tuple[str, bool, str]] = []
    page_errors: list[str] = []
    failed_requests: list[str] = []

    try:
        with sync_playwright() as pw:
            try:
                # slow_mo when visible: gives time to follow each action.
                browser = pw.chromium.launch(
                    headless=headless, slow_mo=0 if headless else 400)
            except Exception as exc:  # noqa: BLE001
                return (f"Error launching the browser: {str(exc).splitlines()[0]}\n"
                        "If browser binaries are missing, run: playwright install chromium")
            page = browser.new_page()
            page.set_default_timeout(_TIMEOUT_MS)
            page.on("pageerror",
                    lambda e: page_errors.append(str(e).splitlines()[0][:200]))
            page.on("response",
                    lambda r: failed_requests.append(f"{r.status} {r.url}")
                    if r.status >= 400 else None)

            ok, info = _run_step(page, f"goto {url}")
            results.append((f"goto {url}", ok, info))
            if ok:
                for raw in steps:
                    if not raw.strip():
                        continue
                    passed, detail = _run_step(page, raw)
                    results.append((raw.strip(), passed, detail))
            browser.close()
    except Exception as exc:  # noqa: BLE001
        results.append(("(browser session)", False, str(exc).splitlines()[0][:200]))

    return _report(url, results, page_errors, failed_requests)
