"""Playwright 执行器 - 支持 headless/headed 模式"""

from ..base_executor import BaseExecutor, Response
from ..proxy_utils import build_playwright_proxy_config, ProxyBandwidthExhausted


class PlaywrightExecutor(BaseExecutor):
    def __init__(self, proxy: str = None, headless: bool = True):
        super().__init__(proxy)
        self.headless = headless
        self._browser = None
        self._context = None
        self._page = None
        self._init()

    def _init(self):
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        launch_opts = {
            "headless": self.headless,
            "args": [
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-gpu",
                "--single-process",
                "--proxy-bypass-list=<-loopback>",
            ],
        }
        if self.proxy:
            launch_opts["proxy"] = build_playwright_proxy_config(self.proxy)
        self._browser = self._pw.chromium.launch(**launch_opts)
        self._context = self._browser.new_context()
        self._page = self._context.new_page()

    def _check_402(self, status_code: int, body_text: str = "") -> None:
        """Kiểm tra response 402 → ban proxy + raise exception."""
        if status_code == 402 or "402 Payment Required" in (body_text or ""):
            if self.proxy:
                from ..proxy_pool import proxy_pool
                proxy_pool.ban_proxy(self.proxy)
                print(f"[PROXY DEAD] Băng thông cạn (402), đang loại bỏ proxy: {self.proxy}")
            raise ProxyBandwidthExhausted(self.proxy or "unknown")

    def get(self, url, *, headers=None, params=None) -> Response:
        import urllib.parse

        if params:
            url = url + "?" + urllib.parse.urlencode(params)
        if headers:
            self._page.set_extra_http_headers(headers)
        try:
            resp = self._page.goto(url)
            content = self._page.content()
            self._check_402(resp.status, content)
            return Response(
                status_code=resp.status,
                text=content,
                headers=dict(resp.headers),
                cookies=self.get_cookies(),
            )
        except ProxyBandwidthExhausted:
            raise
        except Exception as e:
            err_msg = str(e)
            if "402" in err_msg or "Payment Required" in err_msg:
                if self.proxy:
                    from ..proxy_pool import proxy_pool
                    proxy_pool.ban_proxy(self.proxy)
                raise ProxyBandwidthExhausted(self.proxy or "unknown") from e
            raise

    def post(self, url, *, headers=None, params=None, data=None, json=None) -> Response:
        import urllib.parse, json as _json

        if params:
            url = url + "?" + urllib.parse.urlencode(params)
        post_data = None
        content_type = "application/x-www-form-urlencoded"
        if json is not None:
            post_data = _json.dumps(json)
            content_type = "application/json"
        elif data:
            post_data = urllib.parse.urlencode(data)
        h = {"Content-Type": content_type}
        if headers:
            h.update(headers)
        try:
            resp = self._page.request.post(url, headers=h, data=post_data)
            resp_text = resp.text()
            self._check_402(resp.status, resp_text)
            return Response(
                status_code=resp.status,
                text=resp_text,
                headers=dict(resp.headers),
                cookies=self.get_cookies(),
            )
        except ProxyBandwidthExhausted:
            raise
        except Exception as e:
            err_msg = str(e)
            if "402" in err_msg or "Payment Required" in err_msg:
                if self.proxy:
                    from ..proxy_pool import proxy_pool
                    proxy_pool.ban_proxy(self.proxy)
                raise ProxyBandwidthExhausted(self.proxy or "unknown") from e
            raise

    def get_cookies(self) -> dict:
        return {c["name"]: c["value"] for c in self._context.cookies()}

    def set_cookies(self, cookies: dict, domain: str = ".example.com") -> None:
        page_url = self._page.url if self._page else None
        if page_url and page_url.startswith("http"):
            self._context.add_cookies(
                [{"name": k, "value": v, "url": page_url} for k, v in cookies.items()]
            )
        else:
            self._context.add_cookies(
                [
                    {"name": k, "value": v, "domain": domain, "path": "/"}
                    for k, v in cookies.items()
                ]
            )

    def close(self) -> None:
        if self._browser:
            self._browser.close()
        if self._pw:
            self._pw.stop()
