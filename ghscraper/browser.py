"""Stealth browser session: SeleniumBase UC mode + human-like behaviour.

Anti-detection layers:
  * UC mode: patched chromedriver (no `cdc_` markers, navigator.webdriver hidden),
    and `uc_open_with_reconnect`, which detaches the driver while the page loads.
  * Randomised, jittered pauses between navigations and per-keystroke typing.
  * Light random scrolling / dwell time before reading a page.
  * Optional persistent Chrome profile (real cookies, optional manual login) and proxy.
  * Rate-limit / abuse-page detection with exponential back-off.
"""
from __future__ import annotations

import random
import threading
import time
from typing import Callable

from selenium.common.exceptions import WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from seleniumbase import Driver

from .config import Config
from .parsers import detect_block

SEARCH_TRIGGER = 'button[aria-label^="Search or jump to"]'
SEARCH_INPUT = 'input[role="combobox"][aria-label^="Search or jump to"]'


# UC mode patches the chromedriver binary on disk; launching several at once can
# race on that file, and a burst of simultaneous launches looks less human anyway.
_LAUNCH_LOCK = threading.Lock()

# A rate-limit hit by one session applies to every session sharing that exit IP,
# so cooldowns are shared per proxy ("" = direct connection).
_COOLDOWN_LOCK = threading.Lock()
_COOLDOWN_UNTIL: dict[str, float] = {}


class BlockedError(RuntimeError):
    pass


class Cancelled(Exception):
    """Raised inside a worker when the run is being stopped."""


class StealthBrowser:
    def __init__(self, cfg: Config, log: Callable[[str], None] = print, index: int = 0,
                 stop: threading.Event | None = None):
        self.cfg = cfg
        self.log = log
        self.index = index
        self.stop = stop or threading.Event()
        self.driver = None

    def sleep(self, seconds: float) -> None:
        if seconds > 0 and self.stop.wait(seconds):
            raise Cancelled

    # ------------------------------------------------------------ lifecycle
    def __enter__(self) -> "StealthBrowser":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def start(self) -> None:
        kwargs = dict(
            uc=True,
            headless=self.cfg.headless,
            locale_code="en",
            window_size=f"{random.randint(1280, 1600)},{random.randint(820, 1000)}",
        )
        proxy = self.cfg.proxy_for(self.index)
        if proxy:
            kwargs["proxy"] = proxy
        profile = self.cfg.profile_for(self.index)
        if profile:
            kwargs["user_data_dir"] = profile
        with _LAUNCH_LOCK:
            self.driver = Driver(**kwargs)
            time.sleep(random.uniform(1.0, 2.5))
        self.driver.set_page_load_timeout(self.cfg.page_timeout)
        if not self.cfg.headless:
            try:  # cascade windows so parallel sessions don't stack exactly
                self.driver.set_window_position(40 * self.index, 30 * self.index)
            except WebDriverException:
                pass

    def close(self) -> None:
        if self.driver:
            try:
                self.driver.quit()
            except WebDriverException:
                pass
            self.driver = None

    # ------------------------------------------------------------ behaviour
    def pause(self, scale: float = 1.0) -> None:
        lo, hi = sorted((self.cfg.min_delay, self.cfg.max_delay))
        self.sleep(random.uniform(lo, hi) * scale)

    def human_scroll(self) -> None:
        try:
            for _ in range(random.randint(1, 3)):
                self.driver.execute_script("window.scrollBy(0, arguments[0]);", random.randint(250, 750))
                time.sleep(random.uniform(0.25, 0.8))
            if random.random() < 0.4:
                self.driver.execute_script("window.scrollBy(0, arguments[0]);", -random.randint(100, 400))
        except WebDriverException:
            pass

    def human_type(self, element, text: str) -> None:
        mean = max(self.cfg.typing_delay, 0.01)
        for ch in text:
            element.send_keys(ch)
            delay = random.gauss(mean, mean / 3)
            if ch == " ":
                delay *= 1.6
            time.sleep(min(max(delay, 0.02), mean * 4))

    # ------------------------------------------------------------ navigation
    @property
    def html(self) -> str:
        return self.driver.page_source

    @property
    def url(self) -> str:
        return self.driver.current_url

    def open(self, url: str) -> str:
        """Open a URL with retries/back-off; returns the page HTML."""
        ip_key = self.cfg.proxy_for(self.index)
        for attempt in range(1, self.cfg.max_retries + 1):
            with _COOLDOWN_LOCK:
                wait_shared = _COOLDOWN_UNTIL.get(ip_key, 0) - time.time()
            if wait_shared > 0:
                self.sleep(wait_shared + random.uniform(0, 5))
            try:
                self.driver.uc_open_with_reconnect(url, random.uniform(2.0, 3.5))
                self._wait_ready()
                html = self.html
                reason = detect_block(html, self.driver.title)
                if not reason:
                    self.human_scroll()
                    return self.html
                raise BlockedError(reason)
            except (BlockedError, WebDriverException) as e:
                wait = min(20 * 2 ** (attempt - 1), 300) + random.uniform(0, 10)
                msg = str(e).splitlines()[0][:90] if str(e) else ""
                self.log(f"[yellow][B{self.index + 1}] Attempt {attempt}/{self.cfg.max_retries} for {url} "
                         f"failed ({type(e).__name__}: {msg}). Backing off {wait:.0f}s[/]")
                if attempt == self.cfg.max_retries:
                    raise
                if isinstance(e, BlockedError):
                    with _COOLDOWN_LOCK:
                        _COOLDOWN_UNTIL[ip_key] = max(_COOLDOWN_UNTIL.get(ip_key, 0), time.time() + wait)
                    continue  # the shared cooldown check at loop top does the sleeping
                self.sleep(wait)
        raise RuntimeError("unreachable")

    def _wait_ready(self, timeout: float = 15) -> None:
        end = time.time() + timeout
        while time.time() < end:
            try:
                if self.driver.execute_script("return document.readyState") == "complete":
                    return
            except WebDriverException:
                pass
            time.sleep(0.3)

    def wait_for(self, predicate: Callable[[], bool], timeout: float = 15) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            try:
                if predicate():
                    return True
            except WebDriverException:
                pass
            time.sleep(0.4)
        return False

    def wait_for_search(self, timeout: float = 10) -> bool:
        """True once the results payload or the results list is in the DOM."""
        return self.wait_for(lambda: self.driver.execute_script(
            "return !!document.querySelector('script[data-target=\"react-app.embeddedData\"]')"
            " || !!document.querySelector('[data-testid=\"results-list\"]')"), timeout)

    def type_search(self, query: str) -> bool:
        """Use GitHub's header search box like a person would. Returns success."""
        try:
            self.open("https://github.com/")
            self.pause(0.5)
            trigger = self.driver.find_element(By.CSS_SELECTOR, SEARCH_TRIGGER)
            trigger.click()
            if not self.wait_for(lambda: self.driver.find_elements(By.CSS_SELECTOR, SEARCH_INPUT), 8):
                return False
            box = self.driver.find_element(By.CSS_SELECTOR, SEARCH_INPUT)
            time.sleep(random.uniform(0.3, 0.9))
            self.human_type(box, query)
            time.sleep(random.uniform(0.4, 1.0))
            box.send_keys(Keys.ENTER)
            ok = self.wait_for(lambda: "/search" in self.url and
                               self.driver.find_elements(By.CSS_SELECTOR, '[data-testid="results-list"]'), 20)
            if ok:
                self._wait_ready()
                self.human_scroll()
            return bool(ok)
        except WebDriverException as e:
            self.log(f"[yellow]Search-box typing failed ({type(e).__name__}); falling back to URL navigation[/]")
            return False
