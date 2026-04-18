"""Fotor referral registration flow using Playwright only."""

from __future__ import annotations

import random
import string
import time
from typing import Callable

from playwright.sync_api import Page, TimeoutError, sync_playwright


FOTOR_DEFAULT_REF_LINK = "https://www.fotor.com/referrer/ce1yh8e7"
FOTOR_EMAIL_ENTRY_SELECTOR = "div.third_way_button"
FOTOR_EMAIL_INPUT_SELECTOR = "#emailWayStepInputEmail"
FOTOR_NEXT_BUTTON_SELECTOR = "button.email_way_bottom_row_next"
FOTOR_PASSWORD_INPUT_SELECTOR = "#emailWayStepInputPassword"
FOTOR_RECAPTCHA_IFRAME_SELECTOR = 'iframe[src*="recaptcha"]'


def _random_password(length: int = 12) -> str:
    pool = string.ascii_letters + string.digits
    core = "".join(random.choices(pool, k=max(length, 8)))
    return f"Aa1!{core}"


class FotorRegister:
    def __init__(self, proxy: str | None = None, headless: bool = False, log_fn: Callable[[str], None] = print):
        self.proxy = proxy
        self.headless = headless
        self.log = log_fn

    def _wait_for_text(self, page: Page, text: str, timeout: int = 15000) -> None:
        page.get_by_text(text, exact=True).wait_for(state="visible", timeout=timeout)

    def _click_email_entry(self, page: Page) -> None:
        page.wait_for_function(
            """() => [...document.querySelectorAll('div.third_way_button')]
                .some((el) => (el.innerText || '').includes('Continue with Email'))""",
            timeout=20000,
        )
        clicked = page.evaluate(
            """() => {
                const node = [...document.querySelectorAll('div.third_way_button')]
                  .find((el) => (el.innerText || '').includes('Continue with Email'));
                if (!node) return false;
                node.click();
                return true;
            }"""
        )
        if not clicked:
            raise RuntimeError("Fotor email registration entrypoint not found.")

    def _detect_captcha(self, page: Page) -> tuple[bool, str]:
        if page.locator(FOTOR_RECAPTCHA_IFRAME_SELECTOR).count() > 0:
            return True, "recaptcha-enterprise-invisible"
        return False, ""

    def register(self, email: str, password: str | None = None, ref_link: str = "") -> dict:
        resolved_password = password or _random_password()
        resolved_ref_link = ref_link or FOTOR_DEFAULT_REF_LINK
        captcha_detected = False
        captcha_type = ""

        self.log(f"[FOTOR] Opening referral link: {resolved_ref_link}")
        with sync_playwright() as pw:
            launch_opts: dict = {
                "headless": self.headless,
                "args": ["--proxy-bypass-list=<-loopback>"],
            }
            if self.proxy:
                from core.proxy_utils import build_playwright_proxy_config
                proxy_cfg = build_playwright_proxy_config(self.proxy)
                if proxy_cfg:
                    launch_opts["proxy"] = proxy_cfg
                    _server = proxy_cfg.get("server", "")
                    _user = proxy_cfg.get("username", "")
                    self.log(f"[PROXY] server={_server} user={_user or 'none'}")
            browser = pw.chromium.launch(**launch_opts)
            context = browser.new_context(
                locale="en-US",
                timezone_id="America/New_York",
                viewport={"width": 1440, "height": 960},
            )
            # Grant clipboard permissions to avoid permission dialog
            context.grant_permissions(["clipboard-read", "clipboard-write"], origin="https://www.fotor.com")
            context.set_extra_http_headers({"Accept-Language": "en-US,en;q=0.9"})
            page = context.new_page()

            try:
                page.goto(resolved_ref_link, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(5000)

                self.log("[FOTOR] Opening email registration path")
                self._click_email_entry(page)

                self.log(f"[FOTOR] Filling email: {email}")
                page.locator(FOTOR_EMAIL_INPUT_SELECTOR).wait_for(state="visible", timeout=15000)
                page.locator(FOTOR_EMAIL_INPUT_SELECTOR).fill(email)
                page.locator(FOTOR_NEXT_BUTTON_SELECTOR).click()

                self.log("[FOTOR] Waiting for password step")
                page.locator(FOTOR_PASSWORD_INPUT_SELECTOR).wait_for(state="visible", timeout=15000)
                page.locator(FOTOR_PASSWORD_INPUT_SELECTOR).fill(resolved_password)

                captcha_detected, captcha_type = self._detect_captcha(page)
                if captcha_detected:
                    self.log(f"[FOTOR] Captcha detected: {captcha_type}")

                submit = page.locator(FOTOR_NEXT_BUTTON_SELECTOR)
                submit.wait_for(state="visible", timeout=10000)
                self.log("[FOTOR] Submitting create-account step")
                submit.click()
                page.wait_for_timeout(8000)

                # Success markers may vary; do not hard-fail here if the page remains on the same step.
                # The site currently shows invisible reCAPTCHA, which can block unattended completion.
                final_url = page.url
                page_text = page.locator("body").inner_text(timeout=5000)
                if "Set a password" in page_text and "Create my account" in page_text:
                    self.log("[FOTOR] Registration flow did not advance past the password step.")

                return {
                    "email": email,
                    "password": resolved_password,
                    "ref_link": resolved_ref_link,
                    "captcha_detected": captcha_detected,
                    "captcha_type": captcha_type,
                    "final_url": final_url,
                }
            except TimeoutError as exc:
                raise RuntimeError(f"Fotor registration timed out: {exc}") from exc
            finally:
                context.close()
                browser.close()
