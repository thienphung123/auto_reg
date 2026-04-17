"""Fotor platform plugin."""

from __future__ import annotations

import random
import re
import string
import sys
import time
from typing import Any

from playwright.sync_api import BrowserContext, Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

from core.base_mailbox import BaseMailbox, MailboxAccount
from core.base_platform import Account, AccountStatus, BasePlatform, RegisterConfig
from core.registry import register

try:
    from playwright_stealth import stealth_sync
except Exception:
    stealth_sync = None


FOTOR_DEFAULT_REF_LINK = "https://www.fotor.com/referrer/ce1yh8e7"
FOTOR_REWARDS_URL = "https://www.fotor.com/rewards/"
FOTOR_REF_LINK_PATTERN = r"https://www\.fotor\.com/referrer/[A-Za-z0-9_-]+"
FOTOR_EMAIL_INPUT = "#emailWayStepInputEmail"
FOTOR_PASSWORD_INPUT = "#emailWayStepInputPassword"
FOTOR_OTP_INPUT = "#emailWayStepInputVerifyCode"
FOTOR_NEXT_BUTTON = "button.email_way_bottom_row_next"
TEMP_MAILO_URL = "https://temp-mailo.org/"
FOTOR_REF_CODE_PATTERN = r"(?:referrerCode[\"'=:\\s/]+|/referrer/)([A-Za-z0-9_-]{6,})"


def _random_password(length: int = 12) -> str:
    pool = string.ascii_letters + string.digits
    core = "".join(random.choices(pool, k=max(length, 8)))
    return f"Aa1!{core}"


def _console_print(message: str) -> None:
    try:
        print(message)
    except UnicodeEncodeError:
        try:
            sys.stdout.buffer.write((message + "\n").encode("utf-8", errors="replace"))
            sys.stdout.flush()
        except Exception:
            print(message.encode("ascii", errors="ignore").decode("ascii"))


@register
class FotorPlatform(BasePlatform):
    name = "fotor"
    display_name = "Fotor"
    version = "1.0.0"
    supported_executors = ["headed", "headless"]

    def __init__(self, config: RegisterConfig = None, mailbox: BaseMailbox = None):
        super().__init__(config)
        self.mailbox = mailbox

    def _debug_dom_snapshot(self, page: Page, label: str) -> None:
        try:
            payload = page.evaluate(
                """() => {
                    const text = (el) => ((el.innerText || el.value || el.getAttribute('aria-label') || '') + '').trim();
                    const visible = (el) => {
                        if (!el) return false;
                        const style = window.getComputedStyle(el);
                        const rect = el.getBoundingClientRect();
                        return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
                    };
                    const collect = (selector) => [...document.querySelectorAll(selector)]
                        .filter(visible)
                        .slice(0, 20)
                        .map((el) => ({
                            tag: el.tagName,
                            text: text(el).slice(0, 160),
                            id: el.id || '',
                            className: String(el.className || '').slice(0, 160),
                            type: el.getAttribute('type') || '',
                            placeholder: el.getAttribute('placeholder') || '',
                            value: String(el.value || '').slice(0, 120)
                        }));
                    return {
                        url: location.href,
                        inputs: collect('input, textarea'),
                        buttons: collect('button, [role="button"], a, label, div')
                    };
                }"""
            )
            _console_print(f"[FOTOR][DOM][{label}] url={payload.get('url', '')}")
        except Exception as exc:
            _console_print(f"[FOTOR][DOM][{label}] snapshot failed: {exc}")

    def _resolve_mail_provider(self, kwargs: dict[str, Any]) -> str:
        config_source = kwargs.get("config") or self.config or {}
        if isinstance(config_source, RegisterConfig):
            extra = config_source.extra or {}
        elif isinstance(config_source, dict):
            extra = (config_source.get("extra") or {}) if isinstance(config_source.get("extra"), dict) else {}
        else:
            extra = getattr(config_source, "extra", {}) or {}
        return str(extra.get("mail_provider", "api") or "api").strip().lower()

    def _grant_clipboard_permissions(self, context: BrowserContext) -> None:
        try:
            context.grant_permissions(["clipboard-read", "clipboard-write"], origin="https://www.fotor.com")
        except Exception:
            pass

    def _open_email_signup(self, page: Page) -> None:
        candidates = [
            page.locator("div.third_way_button").filter(has_text="Continue with Email").first,
            page.locator("text=Continue with Email").first,
        ]
        for locator in candidates:
            try:
                locator.wait_for(state="visible", timeout=8000)
                locator.scroll_into_view_if_needed(timeout=8000)
                locator.click(force=True)
                return
            except Exception:
                continue

    def _submit_email_step(self, page: Page) -> None:
        candidates = [
            page.locator(FOTOR_NEXT_BUTTON).first,
            page.locator("button:has-text('Continue')").first,
            page.locator("button:has-text('Next')").first,
        ]
        for locator in candidates:
            try:
                locator.wait_for(state="visible", timeout=5000)
                locator.scroll_into_view_if_needed(timeout=5000)
                locator.click(force=True)
                return
            except Exception:
                continue
        raise RuntimeError("Fotor email Continue button was not clickable.")

    def _ensure_terms_checkbox(self, page: Page, timeout_ms: int = 10000) -> bool:
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            for selector in [
                "div.checkBoxSignUp_inner",
                "div.checkBoxSignUp",
                "div.agreementBox div[class*='checkBox']",
            ]:
                try:
                    locator = page.locator(selector).first
                    locator.wait_for(state="visible", timeout=2000)
                    locator.scroll_into_view_if_needed(timeout=2000)
                    locator.click(force=True)
                    page.wait_for_timeout(400)
                    return True
                except Exception:
                    continue
            page.wait_for_timeout(500)
        return False

    def _click_create_account(self, page: Page) -> None:
        candidates = [
            page.locator(FOTOR_NEXT_BUTTON).first,
            page.locator("button:has-text('Create my account')").first,
            page.locator("text=Create my account").first,
        ]
        for locator in candidates:
            try:
                locator.wait_for(state="visible", timeout=5000)
                locator.scroll_into_view_if_needed(timeout=5000)
                locator.click(force=True)
                _console_print("[FOTOR] Đã click nút Create Account thành công")
                return
            except Exception:
                continue
        raise RuntimeError("Fotor Create my account button was not clickable.")

    def _wait_for_login_success(self, page: Page, signup_url: str, timeout_ms: int = 90000) -> None:
        page.wait_for_function(
            """(initialUrl) => {
                const text = (document.body?.innerText || '').toLowerCase();
                const current = window.location.href;
                const otp = document.querySelector('#emailWayStepInputVerifyCode');
                const otpVisible = otp && (() => {
                    const style = window.getComputedStyle(otp);
                    const rect = otp.getBoundingClientRect();
                    return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
                })();
                if (otpVisible) return false;
                if (current !== initialUrl && !current.includes('/events/get-credits/')) return true;
                return text.includes('rewards center')
                    || text.includes('my account')
                    || text.includes('log out')
                    || text.includes('sign out');
            }""",
            arg=signup_url,
            timeout=timeout_ms,
        )

    def _prepare_tempmail_email(self, context: BrowserContext) -> str:
        mail_page = context.new_page()
        try:
            if stealth_sync:
                stealth_sync(mail_page)
        except Exception:
            pass

        try:
            mail_page.goto(TEMP_MAILO_URL, wait_until="domcontentloaded", timeout=30000)
            try:
                mail_page.locator("#cookie_close").click(timeout=2000)
            except Exception:
                pass

            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                for selector in ["#email_id", "input[type='email']", "[data-testid='email']"]:
                    try:
                        locator = mail_page.locator(selector).first
                        locator.wait_for(state="visible", timeout=1500)
                        value = (locator.input_value(timeout=1000) or locator.inner_text(timeout=1000) or "").strip()
                        if "@" in value:
                            _console_print(f"[TEMP-MAILO] Email: {value}")
                            return value
                    except Exception:
                        continue
                try:
                    body_text = mail_page.locator("body").inner_text(timeout=2000)
                    match = re.search(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}", body_text, re.I)
                    if match:
                        _console_print(f"[TEMP-MAILO] Email: {match.group(0)}")
                        return match.group(0)
                except Exception:
                    pass
                mail_page.wait_for_timeout(1000)
        finally:
            try:
                mail_page.close()
            except Exception:
                pass
        raise RuntimeError("temp-mailo did not expose a usable email address.")

    def _get_otp_via_tempmail_ui(self, context: BrowserContext, email: str) -> str:
        mail_page = context.new_page()
        start_time = time.time()
        try:
            if stealth_sync:
                stealth_sync(mail_page)
        except Exception:
            pass

        try:
            _console_print(f"[TEMP-MAILO] Opening inbox UI for {email}")
            mail_page.goto(TEMP_MAILO_URL, wait_until="domcontentloaded", timeout=30000)
            try:
                mail_page.locator("#cookie_close").click(timeout=2000)
            except Exception:
                pass

            inbox_locator = mail_page.locator("#email_id, body").first
            inbox_locator.wait_for(state="visible", timeout=10000)

            for attempt in range(1, 11):
                if time.time() - start_time > 60:
                    _console_print("[TEMP-MAILO] Timeout 60s while waiting for Fotor OTP.")
                    try:
                        mail_page.close()
                    except Exception:
                        pass
                    raise RuntimeError("Timed out waiting for Fotor OTP on temp-mailo.org after 60s.")
                _console_print(f"[TEMP-MAILO] Refresh attempt {attempt}/10")
                try:
                    refresh_button = mail_page.locator("text=Refresh").first
                    refresh_button.wait_for(state="visible", timeout=2000)
                    refresh_button.scroll_into_view_if_needed(timeout=2000)
                    refresh_button.click(force=True)
                except Exception:
                    try:
                        mail_page.reload(wait_until="domcontentloaded", timeout=10000)
                    except Exception:
                        pass

                mail_page.wait_for_timeout(5000)

                mail_locator = None
                for selector in [
                    'text="Fotor"',
                    "text=/Fotor/i",
                    "text=/verification/i",
                    "text=/check your email/i",
                ]:
                    try:
                        candidate = mail_page.locator(selector).first
                        candidate.wait_for(state="visible", timeout=2000)
                        mail_locator = candidate
                        break
                    except Exception:
                        continue

                if not mail_locator:
                    _console_print("[TEMP-MAILO] Chưa thấy thư Fotor")
                    continue

                _console_print("[TEMP-MAILO] Đã thấy thư Fotor")
                try:
                    mail_locator.scroll_into_view_if_needed(timeout=2000)
                    mail_locator.click(force=True)
                except Exception:
                    mail_page.reload(wait_until="domcontentloaded", timeout=10000)
                    continue

                _console_print("[TEMP-MAILO] Đã mở thư Fotor")
                otp = self._extract_tempmail_otp_from_open_message(mail_page)
                if otp:
                    _console_print(f"[TEMP-MAILO] OTP là {otp}")
                    return otp

                _console_print("[TEMP-MAILO] Đã mở thư nhưng chưa móc được OTP")

            raise RuntimeError("Timed out waiting for Fotor OTP on temp-mailo.org.")
        except Exception:
            try:
                mail_page.close()
            except Exception:
                pass
            raise

    def _extract_tempmail_otp_from_open_message(self, mail_page: Page) -> str:
        for probe in range(1, 9):
            _console_print(f"[TEMP-MAILO] Quét nội dung thư lần {probe}/8")
            mail_page.wait_for_timeout(1000)

            try:
                iframe_count = mail_page.locator("iframe").count()
            except Exception:
                iframe_count = 0
            for index in range(iframe_count):
                try:
                    frame_text = mail_page.frame_locator("iframe").nth(index).locator("body").inner_text(timeout=2500)
                    match = re.search(r"\b\d{6}\b", frame_text or "")
                    if match:
                        return match.group(0)
                except Exception:
                    continue

            for selector in [
                "div:has-text('Please Confirm Your Email Address')",
                "div:has-text('verification code')",
                "div:has-text('Verify Your Email Address for Fotor Registration')",
                "div:has-text('Fotor Support')",
                "div[class*='mail']",
                "div[class*='message']",
                "div[class*='content']",
                "main",
                "article",
                "body",
            ]:
                try:
                    text = mail_page.locator(selector).first.inner_text(timeout=3000)
                    match = re.search(r"\b\d{6}\b", text or "")
                    if match:
                        return match.group(0)
                except Exception:
                    continue

            try:
                text = mail_page.evaluate(
                    """() => {
                        const nodes = [...document.querySelectorAll('div, section, article, main, p, span, td')];
                        return nodes
                            .map((node) => (node.innerText || '').trim())
                            .filter(Boolean)
                            .sort((a, b) => b.length - a.length)
                            .slice(0, 20)
                            .join('\\n');
                    }"""
                )
                match = re.search(r"\b\d{6}\b", text or "")
                if match:
                    return match.group(0)
            except Exception:
                pass

            try:
                html = mail_page.content()
                match = re.search(r"\b\d{6}\b", html or "")
                if match:
                    return match.group(0)
            except Exception:
                pass

        return ""

    def _wait_for_mailbox_otp(
        self,
        mailbox: BaseMailbox,
        mailbox_account: MailboxAccount,
        before_ids: set | None,
    ) -> str:
        code = mailbox.wait_for_code(
            mailbox_account,
            keyword="fotor",
            timeout=60,
            before_ids=before_ids,
            code_pattern=r"(?<![a-zA-Z0-9])(\d{6})(?![a-zA-Z0-9])",
        )
        normalized = str(code or "").strip()
        if not normalized or normalized == "000000":
            raise RuntimeError(f"Mailbox returned invalid OTP: {normalized!r}")
        if not re.fullmatch(r"\d{6}", normalized):
            raise RuntimeError(f"Mailbox returned malformed OTP: {normalized!r}")
        return normalized

    def _otp_rejected(self, fotor_page: Page) -> bool:
        try:
            error_text = fotor_page.locator("text=/Invalid|incorrect|please try again/i").first
            error_text.wait_for(state="visible", timeout=3000)
            return True
        except Exception:
            return False

    def _submit_otp_and_confirm(
        self,
        *,
        fotor_page: Page,
        otp: str,
        signup_url: str,
        mailbox: BaseMailbox | None,
        mailbox_account: MailboxAccount | None,
        before_ids: set | None,
    ) -> None:
        otp_field = fotor_page.locator(FOTOR_OTP_INPUT).first
        otp_field.wait_for(state="visible", timeout=5000)
        try:
            otp_field.scroll_into_view_if_needed(timeout=3000)
        except Exception:
            pass
        otp_field.fill(str(otp))

        verify_btn = fotor_page.locator(FOTOR_NEXT_BUTTON).first
        verify_btn.wait_for(state="visible", timeout=5000)
        try:
            verify_btn.scroll_into_view_if_needed(timeout=3000)
        except Exception:
            pass
        fotor_page.wait_for_timeout(300)
        verify_btn.click(force=True)

        try:
            self._wait_for_login_success(fotor_page, signup_url, timeout_ms=15000)
            return
        except PlaywrightTimeoutError:
            if self._otp_rejected(fotor_page):
                raise RuntimeError(f"Fotor rejected OTP {otp}.")
            raise

    def _extract_rewards_link(self, fotor_page: Page) -> dict[str, str]:
        if "/rewards/" not in fotor_page.url:
            fotor_page.goto(FOTOR_REWARDS_URL, wait_until="domcontentloaded", timeout=30000)
        fotor_page.wait_for_timeout(3000)
        deadline = time.time() + 20

        while time.time() < deadline:
            try:
                fotor_page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            except Exception:
                pass
            fotor_page.wait_for_timeout(1200)
            for selector in ["text=Copy link", "text=Copy Link", "button:has-text('Copy')"]:
                try:
                    fotor_page.locator(selector).first.scroll_into_view_if_needed(timeout=3000)
                    break
                except Exception:
                    continue

            result = ""
            try:
                for selector in ["text=Copy link", "text=Copy Link", "button:has-text('Copy')"]:
                    try:
                        copy_btn = fotor_page.locator(selector).first
                        copy_btn.wait_for(state="visible", timeout=2500)
                        copy_btn.click(force=True, timeout=2000)
                        fotor_page.wait_for_timeout(1200)
                        clipboard_value = fotor_page.evaluate(
                            """async () => {
                                try {
                                    return await navigator.clipboard.readText();
                                } catch (e) {
                                    return '';
                                }
                            }"""
                        )
                        if isinstance(clipboard_value, str):
                            match = re.search(FOTOR_REF_LINK_PATTERN, clipboard_value)
                            if match:
                                result = match.group(0)
                                break
                    except Exception:
                        continue
            except Exception:
                pass

            if not result:
                try:
                    result = fotor_page.evaluate(
                        """() => {
                            const pattern = /https:\\/\\/www\\.fotor\\.com\\/referrer\\/[A-Za-z0-9_-]+/i;
                            const attrs = ['href', 'value', 'data-clipboard-text', 'data-copy', 'data-link'];
                            const nodes = [...document.querySelectorAll('*')];
                            for (const node of nodes) {
                                for (const attr of attrs) {
                                    const value = node.getAttribute && node.getAttribute(attr);
                                    if (value && pattern.test(value)) {
                                        return value.match(pattern)[0];
                                    }
                                }
                                if ('value' in node && typeof node.value === 'string' && pattern.test(node.value)) {
                                    return node.value.match(pattern)[0];
                                }
                                const text = (node.innerText || node.textContent || '').trim();
                                if (text && pattern.test(text)) {
                                    return text.match(pattern)[0];
                                }
                            }
                            return '';
                        }"""
                    ) or ""
                except Exception:
                    result = ""

            if not result:
                for probe in [
                    lambda: fotor_page.content(),
                    lambda: fotor_page.locator("body").inner_text(timeout=5000),
                ]:
                    try:
                        blob = probe() or ""
                        match = re.search(FOTOR_REF_LINK_PATTERN, blob)
                        if match:
                            result = match.group(0)
                            break
                        code_match = re.search(FOTOR_REF_CODE_PATTERN, blob)
                        if code_match:
                            result = f"https://www.fotor.com/referrer/{code_match.group(1)}"
                            break
                    except Exception:
                        continue

            if result:
                _console_print(f"[FOTOR] Referrer link: {result}")
                return {"text": result, "href": result}

            fotor_page.wait_for_timeout(2000)

        try:
            debug_text = fotor_page.locator("body").inner_text(timeout=5000)
            compact = re.sub(r"\s+", " ", debug_text or "")[:800]
            _console_print(f"[FOTOR][REWARDS] body snippet: {compact}")
        except Exception:
            pass
        try:
            debug_html = fotor_page.content()
            idx = debug_html.lower().find("referr")
            if idx >= 0:
                _console_print(f"[FOTOR][REWARDS] html ref snippet: {debug_html[max(0, idx-120):idx+240]}")
        except Exception:
            pass
        raise RuntimeError("Fotor referrer link not found on rewards page.")

    def create_account(
        self,
        context: BrowserContext | None = None,
        email: str = None,
        mailbox: BaseMailbox | None = None,
        password: str = None,
        headless: bool = False,
        **kwargs,
    ) -> dict:
        active_mailbox = mailbox or self.mailbox
        mail_provider = self._resolve_mail_provider(kwargs)
        resolved_password = password or _random_password()
        ref_link = (
            self.config.extra.get("fotor_ref_link")
            or self.config.extra.get("ref_link")
            or FOTOR_DEFAULT_REF_LINK
        )

        browser = None
        mailbox_account: MailboxAccount | None = None
        before_ids: set | None = None

        if not email and active_mailbox and mail_provider != "tempmail":
            mailbox_account = active_mailbox.get_email()
            email = mailbox_account.email
            try:
                before_ids = active_mailbox.get_current_ids(mailbox_account)
            except Exception:
                before_ids = set()

        if context is None:
            with sync_playwright() as pw:
                launch_opts = {"headless": headless}
                if self.config.proxy:
                    launch_opts["proxy"] = {"server": self.config.proxy}
                browser = pw.chromium.launch(**launch_opts)
                context = browser.new_context(
                    locale="en-US",
                    timezone_id="America/New_York",
                    viewport={"width": 1440, "height": 960},
                )
                self._grant_clipboard_permissions(context)
                if not email and mail_provider == "tempmail":
                    email = self._prepare_tempmail_email(context)
                if not email:
                    raise RuntimeError("Fotor create_account requires an email or an available mailbox.")
                result = self._create_account_with_context(
                    context=context,
                    email=email,
                    mailbox=active_mailbox,
                    mailbox_account=mailbox_account,
                    before_ids=before_ids,
                    password=resolved_password,
                    ref_link=ref_link,
                    mail_provider=mail_provider,
                )
                context.close()
                browser.close()
                return result

        self._grant_clipboard_permissions(context)
        if not email and mail_provider == "tempmail":
            email = self._prepare_tempmail_email(context)
        if not email:
            raise RuntimeError("Fotor create_account requires an email or an available mailbox.")
        return self._create_account_with_context(
            context=context,
            email=email,
            mailbox=active_mailbox,
            mailbox_account=mailbox_account,
            before_ids=before_ids,
            password=resolved_password,
            ref_link=ref_link,
            mail_provider=mail_provider,
        )

    def _create_account_with_context(
        self,
        *,
        context: BrowserContext,
        email: str,
        mailbox: BaseMailbox | None,
        mailbox_account: MailboxAccount | None,
        before_ids: set | None,
        password: str,
        ref_link: str,
        mail_provider: str,
    ) -> dict:
        fotor_page = context.new_page()
        result = {
            "email": email,
            "password": password,
            "ref_link": ref_link,
            "final_url": "",
            "rewards_link_text": "",
            "rewards_link_href": "",
            "mail_provider": mail_provider,
        }

        try:
            _console_print(f"[FOTOR] Opening referral link: {ref_link}")
            fotor_page.goto(ref_link, wait_until="domcontentloaded", timeout=60000)
            fotor_page.wait_for_timeout(1500)
            self._open_email_signup(fotor_page)

            fotor_page.bring_to_front()
            _console_print(f"[FOTOR] Filling email: {email}")
            fotor_page.locator(FOTOR_EMAIL_INPUT).wait_for(state="visible", timeout=15000)
            fotor_page.locator(FOTOR_EMAIL_INPUT).fill(email)

            _console_print("[FOTOR] Clicking Continue")
            self._submit_email_step(fotor_page)

            _console_print("[FOTOR] Waiting for password field")
            fotor_page.locator(FOTOR_PASSWORD_INPUT).wait_for(state="visible", timeout=30000)
            fotor_page.locator(FOTOR_PASSWORD_INPUT).fill(password)

            _console_print("[FOTOR] Ticking terms checkbox")
            if not self._ensure_terms_checkbox(fotor_page, timeout_ms=10000):
                raise RuntimeError("Fotor terms checkbox could not be checked.")

            _console_print("[FOTOR] Clicking Create my account")
            self._click_create_account(fotor_page)

            signup_url = fotor_page.url
            _console_print("[FOTOR] Waiting for OTP input to appear")
            fotor_page.wait_for_selector(FOTOR_OTP_INPUT, state="visible", timeout=90000)

            if mail_provider == "tempmail":
                otp = self._get_otp_via_tempmail_ui(context, email)
            else:
                if mailbox is None:
                    raise RuntimeError(f"Mailbox is required for mail_provider={mail_provider}.")
                if mailbox_account is None:
                    mailbox_account = mailbox.get_email()
                    if not result["email"]:
                        result["email"] = mailbox_account.email
                otp = self._wait_for_mailbox_otp(
                    mailbox=mailbox,
                    mailbox_account=mailbox_account,
                    before_ids=before_ids,
                )

            _console_print(f"[FOTOR] Filling OTP: {otp}")
            _console_print("[FOTOR] Waiting for login success checkpoint")
            self._submit_otp_and_confirm(
                fotor_page=fotor_page,
                otp=otp,
                signup_url=signup_url,
                mailbox=mailbox,
                mailbox_account=mailbox_account,
                before_ids=before_ids,
            )

            _console_print("[FOTOR] Login confirmed, opening rewards page")
            fotor_page.goto(FOTOR_REWARDS_URL, wait_until="domcontentloaded", timeout=60000)
            rewards_data = self._extract_rewards_link(fotor_page)
            result["rewards_link_text"] = rewards_data.get("text", "")
            result["rewards_link_href"] = rewards_data.get("href", "")
            result["final_url"] = fotor_page.url
            return result
        except PlaywrightTimeoutError as exc:
            result["final_url"] = fotor_page.url
            self._debug_dom_snapshot(fotor_page, "timeout")
            _console_print(f"[FOTOR] Timeout during create_account: {exc}")
            raise
        except Exception as exc:
            result["final_url"] = fotor_page.url
            self._debug_dom_snapshot(fotor_page, "error")
            _console_print(f"[FOTOR] Error during create_account: {exc}")
            raise

    def register(self, email: str, password: str = None) -> Account:
        headless = self.config.extra.get("headless", False) if self.config else False
        result = self.create_account(
            email=email,
            password=password,
            mailbox=self.mailbox,
            config=self.config,
            headless=headless,
        )
        return Account(
            platform="fotor",
            email=result["email"],
            password=result["password"],
            status=AccountStatus.REGISTERED,
            extra={
                "ref_link": result.get("ref_link", ""),
                "final_url": result.get("final_url", ""),
                "rewards_link_text": result.get("rewards_link_text", ""),
                "rewards_link_href": result.get("rewards_link_href", ""),
                "mail_provider": result.get("mail_provider", ""),
            },
        )

    def check_valid(self, account: Account) -> bool:
        return bool(account.email and account.password)
