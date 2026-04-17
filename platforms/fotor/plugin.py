"""Fotor platform plugin."""

import random
import re
import string
import sys
import time

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

from core.base_mailbox import BaseMailbox
from core.base_platform import Account, AccountStatus, BasePlatform, RegisterConfig
from core.registry import register


FOTOR_DEFAULT_REF_LINK = "https://www.fotor.com/referrer/ce1yh8e7"
FOTOR_REWARDS_URL = "https://www.fotor.com/rewards/"
FOTOR_REF_LINK_PATTERN = r"https://www\.fotor\.com/referrer/[A-Za-z0-9_-]+"
FOTOR_PASSWORD_INPUT = "#emailWayStepInputPassword"
FOTOR_OTP_INPUT = "#emailWayStepInputVerifyCode"
TEMP_MAILO_URL = "https://temp-mailo.org/"


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
                    const isVisible = (el) => {
                        if (!el) return false;
                        const style = window.getComputedStyle(el);
                        const rect = el.getBoundingClientRect();
                        return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
                    };
                    const collect = (selector) => [...document.querySelectorAll(selector)]
                        .filter(isVisible)
                        .slice(0, 20)
                        .map((el) => ({
                            tag: el.tagName,
                            text: text(el).slice(0, 160),
                            id: el.id || '',
                            className: (el.className || '').toString().slice(0, 160),
                            type: el.getAttribute('type') || '',
                            placeholder: el.getAttribute('placeholder') || '',
                            value: (el.value || '').toString().slice(0, 120)
                        }));
                    return {
                        url: location.href,
                        inputs: collect('input, textarea'),
                        buttons: collect('button, [role="button"], a, label, div')
                    };
                }"""
            )
            # Debug logs (uncomment if needed for troubleshooting)
            # print(f"[FOTOR][DOM][{label}] url={payload.get('url', '')}")
            # print(f"[FOTOR][DOM][{label}] inputs={payload.get('inputs', [])}")
            # print(f"[FOTOR][DOM][{label}] buttons={payload.get('buttons', [])}")
        except Exception as exc:
            print(f"[FOTOR][DOM][{label}] snapshot failed: {exc}")

    def _click_first_visible(self, page: Page, selectors: list[str], *, timeout_ms: int = 3000) -> bool:
        for selector in selectors:
            try:
                locator = page.locator(selector).first
                locator.wait_for(state="visible", timeout=timeout_ms)
                locator.scroll_into_view_if_needed(timeout=timeout_ms)
                locator.click(force=True)
                return True
            except Exception:
                continue
        return False

    def _open_email_signup(self, fotor_page: Page) -> None:
        candidates = [
            fotor_page.locator("div.third_way_button").filter(has_text="Continue with Email").first,
            fotor_page.locator("text=Continue with Email").first,
        ]
        for locator in candidates:
            try:
                locator.wait_for(state="visible", timeout=8000)
                locator.scroll_into_view_if_needed(timeout=8000)
                locator.click(force=True)
                return
            except Exception:
                continue
        self._wait_for_any_button_and_click(fotor_page, ["continue with email"], timeout_ms=12000)

    def _submit_email_step(self, fotor_page: Page) -> None:
        candidates = [
            fotor_page.locator("button.email_way_bottom_row_next").first,
            fotor_page.locator("button:has-text('Continue')").first,
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

    def _wait_for_any_button_and_click(self, page: Page, labels: list[str], *, timeout_ms: int = 20000) -> str:
        wanted = [label.lower() for label in labels]
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            try:
                clicked = page.evaluate(
                    """(labels) => {
                        const normalize = (value) => (value || '').replace(/\s+/g, ' ').trim().toLowerCase();
                        const isVisible = (el) => {
                            if (!el) return false;
                            const style = window.getComputedStyle(el);
                            const rect = el.getBoundingClientRect();
                            return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
                        };
                        const nodes = [...document.querySelectorAll('button, [role="button"], a, div.third_way_button, label, div')];
                        for (const node of nodes) {
                            const content = normalize(node.innerText || node.textContent || node.getAttribute('aria-label') || '');
                            if (!content || !isVisible(node)) continue;
                            if (labels.some((label) => content === label || content.includes(label))) {
                                node.click();
                                return content;
                            }
                        }
                        return '';
                    }""",
                    wanted,
                )
                if clicked:
                    return clicked
            except Exception:
                pass
            page.wait_for_timeout(500)
        raise RuntimeError(f"Could not find a visible button matching: {labels}")

    def _prepare_temp_mailo(self, mail_page: Page) -> str:
        mail_page.goto(TEMP_MAILO_URL, wait_until="domcontentloaded", timeout=60000)
        mail_page.wait_for_timeout(3000)
        try:
            mail_page.locator("#cookie_close").click(timeout=2000)
        except Exception:
            pass

        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                email = mail_page.evaluate(
                    """() => {
                        const isVisible = (el) => {
                            if (!el) return false;
                            const style = window.getComputedStyle(el);
                            const rect = el.getBoundingClientRect();
                            return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
                        };

                        const direct = [...document.querySelectorAll('#email_id')]
                            .find((el) => isVisible(el) && /@/.test((el.innerText || '').trim()));
                        if (direct) return (direct.innerText || '').trim();

                        const bodyText = document.body?.innerText || '';
                        const match = bodyText.match(/[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}/i);
                        return match ? match[0] : '';
                    }"""
                )
                if email and "@" in email:
                    print(f"[TEMP-MAILO] Email: {email}")
                    return email
            except Exception:
                pass
            mail_page.wait_for_timeout(1000)

        raise RuntimeError("temp-mailo did not expose a usable email address.")

    def _wait_for_fotor_mail_and_extract_otp(self, mail_page: Page, email: str = "", *, timeout_ms: int = 15000) -> str:
        """Extract OTP from Fotor email with reload loop (max 15s)"""
        pattern = re.compile(r"\b\d{6}\b")
        deadline = time.monotonic() + timeout_ms / 1000
        reload_attempt = 0
        max_reload_attempts = 5
        
        while time.monotonic() < deadline and reload_attempt < max_reload_attempts:
            reload_attempt += 1
            try:
                mail_page.bring_to_front()
            except Exception:
                pass

            # Refresh email list
            try:
                refresh_btn = mail_page.locator("text=Refresh").first
                refresh_btn.wait_for(state="visible", timeout=3000)
                refresh_btn.click(force=True, timeout=3000)
            except Exception:
                try:
                    mail_page.reload(wait_until="domcontentloaded", timeout=10000)
                except Exception:
                    pass

            mail_page.wait_for_timeout(2000)
            fotor_email_locator = None
            found_email = False
            
            # Try multiple strategies to find Fotor email
            for strategy_num in range(1, 4):
                if found_email:
                    break
                try:
                    if strategy_num == 1:
                        fotor_email_locator = mail_page.locator('text=/Fotor/i').first
                    elif strategy_num == 2:
                        fotor_email_locator = mail_page.locator('.mail-item, li, tr, [class*="mail"], [class*="item"]').filter(
                            has_text=re.compile('Fotor', re.IGNORECASE)
                        ).first
                    else:  # strategy_num == 3
                        fotor_email_locator = mail_page.locator('div, section, article, [role="listitem"]').filter(
                            has_text=re.compile('Fotor', re.IGNORECASE)
                        ).first
                    
                    fotor_email_locator.wait_for(state="visible", timeout=3000)
                    found_email = True
                except Exception:
                    continue
            
            if found_email and fotor_email_locator:
                try:
                    fotor_email_locator.scroll_into_view_if_needed(timeout=2000)
                    mail_page.wait_for_timeout(500)
                    fotor_email_locator.click(force=True, timeout=3000)
                    mail_page.wait_for_timeout(3000)
                    
                    # Extract OTP from email content
                    body_text = ""
                    
                    # Try iframe
                    try:
                        frames = mail_page.locator("iframe")
                        for i in range(frames.count()):
                            try:
                                frame = mail_page.frame_locator("iframe").nth(i)
                                iframe_text = frame.locator("body").inner_text(timeout=3000)
                                if "fotor" in iframe_text.lower() or len(iframe_text) > 100:
                                    body_text = iframe_text
                                    break
                            except Exception:
                                continue
                    except Exception:
                        pass
                    
                    # Try common containers if iframe didn't work
                    if not body_text or len(body_text) < 50:
                        for selector in ['.mail-body', '.email-content', '.mail-content', '[role="main"]', '.content']:
                            try:
                                element = mail_page.locator(selector).first
                                element.wait_for(state="visible", timeout=2000)
                                content = element.inner_text(timeout=3000)
                                if content and len(content) > 50:
                                    body_text = content
                                    break
                            except Exception:
                                continue
                    
                    # Fallback: get largest text container
                    if not body_text or len(body_text) < 50:
                        try:
                            body_text = mail_page.evaluate("""() => {
                                const containers = document.querySelectorAll('div, section, article, [role="main"], .container');
                                let largest = '';
                                for (const el of containers) {
                                    const text = el.innerText || '';
                                    if (text.toLowerCase().includes('fotor') && text.length > largest.length) {
                                        largest = text;
                                    }
                                }
                                return largest || document.body.innerText;
                            }""")
                        except Exception:
                            pass
                    
                    # Search for OTP
                    match = pattern.search(body_text or "")
                    if match:
                        otp = match.group(0)
                        print(f"[TEMP-MAILO] ✓✓✓ OTP found: {otp}")
                        return otp
                except Exception:
                    pass
            
            # Wait before retry
            remaining_time = deadline - time.monotonic()
            if remaining_time > 1:
                wait_time = min(2000, int(remaining_time * 500))
                mail_page.wait_for_timeout(wait_time)
            else:
                break

        # Timeout
        email_domain = email.split('@')[1] if '@' in email else email
        raise RuntimeError(f"[TEMP-MAILO] Timeout - Fotor OTP not received at {email_domain}")

    def _ensure_terms_checkbox(self, fotor_page: Page, *, timeout_ms: int = 10000) -> bool:
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            try:
                box = fotor_page.locator("div.checkBoxSignUp_inner").first
                box.wait_for(state="visible", timeout=2000)
                box.scroll_into_view_if_needed(timeout=2000)
                box.click(force=True)
                fotor_page.wait_for_timeout(500)
                return True
            except Exception:
                try:
                    terms_text = fotor_page.locator("text=/By continuing/i").first
                    terms_text.wait_for(state="visible", timeout=2000)
                    terms_text.scroll_into_view_if_needed(timeout=2000)
                except Exception:
                    pass
            fotor_page.wait_for_timeout(500)
        return False

    def _fill_otp_code(self, fotor_page: Page, code: str) -> None:
        field = fotor_page.locator(FOTOR_OTP_INPUT).first
        field.wait_for(state="visible", timeout=5000)
        field.scroll_into_view_if_needed(timeout=5000)
        field.fill(str(code))

    def _wait_for_login_success(self, fotor_page: Page, signup_url: str, *, timeout_ms: int = 90000) -> None:
        fotor_page.wait_for_function(
            """(signupUrl) => {
                const bodyText = (document.body?.innerText || '').toLowerCase();
                const currentUrl = window.location.href;
                const otpInput = document.querySelector('#emailWayStepInputVerifyCode');
                const otpVisible = otpInput && (() => {
                    const style = window.getComputedStyle(otpInput);
                    const rect = otpInput.getBoundingClientRect();
                    return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
                })();
                if (otpVisible) return false;
                if (currentUrl !== signupUrl && !currentUrl.includes('/events/get-credits/')) return true;
                if (bodyText.includes('rewards center')) return true;
                if (bodyText.includes('my account')) return true;
                if (bodyText.includes('log out')) return true;
                return false;
            }""",
            arg=signup_url,
            timeout=timeout_ms,
        )

    def _extract_rewards_link(self, fotor_page: Page) -> dict:
        """Extract referrer link from /rewards/ page"""
        
        # Navigate to rewards if not already there
        if "/rewards/" not in fotor_page.url:
            fotor_page.goto(FOTOR_REWARDS_URL, wait_until="domcontentloaded", timeout=30000)
            fotor_page.wait_for_timeout(2000)
        
        # Scroll to see Refer Friends section
        fotor_page.evaluate("window.scrollBy(0, 3000)")
        fotor_page.wait_for_timeout(2000)
        
        pattern = FOTOR_REF_LINK_PATTERN
        result = ""
        
        # Click Copy link button
        try:
            copy_btn = fotor_page.locator('text=Copy link').first
            copy_btn.wait_for(state="visible", timeout=3000)
            copy_btn.click(force=True, timeout=2000)
            fotor_page.wait_for_timeout(1500)
        except Exception:
            raise RuntimeError("Copy link button not found on rewards page")
        
        # Read clipboard using Clipboard API (permissions already granted)
        try:
            clipboard_content = fotor_page.evaluate("""async () => {
                try {
                    const text = await navigator.clipboard.readText();
                    return text || '';
                } catch (e) {
                    return '';
                }
            }""")
            
            if clipboard_content and "referrer" in clipboard_content.lower() and "fotor.com" in clipboard_content:
                result = clipboard_content.strip()
        except Exception:
            pass
        
        # Fallback: Search page HTML
        if not result:
            try:
                html = fotor_page.content()
                match = re.search(pattern, html)
                if match:
                    result = match.group(0)
            except Exception:
                pass
        
        # Fallback: Search page text
        if not result:
            try:
                all_text = fotor_page.evaluate("() => document.body.innerText")
                match = re.search(pattern, all_text)
                if match:
                    result = match.group(0)
            except Exception:
                pass
        
        if not result:
            raise RuntimeError("Fotor referrer link not found on rewards page.")
        
        print(f"[FOTOR] ✓✓✓ Referrer link: {result}")
        return {"text": result, "href": result}

    def create_account(self, email: str = None, password: str = None, headless: bool = True) -> dict:
        resolved_password = password or _random_password()
        ref_link = (
            self.config.extra.get("fotor_ref_link")
            or self.config.extra.get("ref_link")
            or FOTOR_DEFAULT_REF_LINK
        )
        log = getattr(self, "_log_fn", print)

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
            # Grant clipboard permissions to avoid permission dialog
            context.grant_permissions(["clipboard-read", "clipboard-write"], origin="https://www.fotor.com")
            fotor_page = context.new_page()
            mail_page = context.new_page()

            result = {
                "email": "",
                "password": resolved_password,
                "ref_link": ref_link,
                "final_url": "",
                "rewards_link_text": "",
                "rewards_link_href": "",
            }

            try:
                mail_email = self._prepare_temp_mailo(mail_page)
                result["email"] = email or mail_email

                fotor_page.goto(ref_link, wait_until="domcontentloaded", timeout=60000)
                fotor_page.wait_for_timeout(1500)
                try:
                    self._open_email_signup(fotor_page)
                except Exception:
                    log("[FOTOR] Continue with Email entry not found, trying direct sign-up form.")

                fotor_page.bring_to_front()
                log(f"[FOTOR] Filling email: {result['email']}")
                fotor_page.locator("#emailWayStepInputEmail").wait_for(state="visible", timeout=15000)
                fotor_page.locator("#emailWayStepInputEmail").fill(result["email"])

                log("[FOTOR] Clicking Continue")
                self._submit_email_step(fotor_page)
                log("[FOTOR] Email step submitted")

                log("[FOTOR] Waiting for password field")
                fotor_page.locator(FOTOR_PASSWORD_INPUT).wait_for(state="visible", timeout=30000)
                fotor_page.locator(FOTOR_PASSWORD_INPUT).fill(resolved_password)

                log("[FOTOR] Ticking terms checkbox")
                if not self._ensure_terms_checkbox(fotor_page, timeout_ms=10000):
                    raise RuntimeError("Fotor terms checkbox could not be checked.")

                log("[FOTOR] Clicking Create my account")
                submit_btn = fotor_page.locator("button.email_way_bottom_row_next").first
                submit_btn.wait_for(state="visible", timeout=10000)
                submit_btn.scroll_into_view_if_needed(timeout=10000)
                submit_btn.click(force=True)

                signup_url = fotor_page.url
                log("[FOTOR] Waiting for OTP input to appear")
                fotor_page.wait_for_selector(FOTOR_OTP_INPUT, state="visible", timeout=30000)

                otp = self._wait_for_fotor_mail_and_extract_otp(mail_page, email=result["email"], timeout_ms=15000)

                fotor_page.bring_to_front()
                log(f"[FOTOR] Filling OTP: {otp}")
                self._fill_otp_code(fotor_page, otp)
                verify_btn = fotor_page.locator("button.email_way_bottom_row_next").first
                verify_btn.scroll_into_view_if_needed(timeout=5000)
                verify_btn.click(force=True)

                log("[FOTOR] Waiting for login success checkpoint")
                self._wait_for_login_success(fotor_page, signup_url, timeout_ms=30000)

                log("[FOTOR] Login confirmed, opening rewards page")
                fotor_page.goto(FOTOR_REWARDS_URL, wait_until="domcontentloaded", timeout=60000)
                rewards_data = self._extract_rewards_link(fotor_page)
                result["rewards_link_text"] = rewards_data.get("text", "")
                result["rewards_link_href"] = rewards_data.get("href", "")
                result["final_url"] = fotor_page.url
            except PlaywrightTimeoutError as exc:
                result["final_url"] = fotor_page.url
                self._debug_dom_snapshot(fotor_page, "timeout")
                print(f"[FOTOR] Timeout during create_account: {exc}")
                raise  # Re-raise để test script retry
            except Exception as exc:
                result["final_url"] = fotor_page.url
                self._debug_dom_snapshot(fotor_page, "error")
                print(f"[FOTOR] Error during create_account: {exc}")
                raise  # Re-raise để test script retry
            finally:
                context.close()
                browser.close()

            return result

    def register(self, email: str, password: str = None) -> Account:
        headless = self.config.extra.get("headless", True) if self.config else True
        result = self.create_account(email=email, password=password, headless=headless)
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
            },
        )

    def check_valid(self, account: Account) -> bool:
        return bool(account.email and account.password)
