#!/usr/bin/env python3
"""
手动登录获取 Token 工具
生成 OAuth 授权 URL，用户手动登录后提取 token
"""

import sys
import json
import hashlib
import secrets
import base64
import urllib.parse
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler

# OAuth 配置
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
REDIRECT_URI = "http://localhost:1455/auth/callback"
SCOPE = "openid profile email offline_access"
AUTH_URL = "https://auth.openai.com/oauth/authorize"
TOKEN_URL = "https://auth.openai.com/oauth/token"

def _b64url_no_pad(raw: bytes) -> str:
    """Base64 URL 编码（无填充）"""
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

def _sha256_b64url_no_pad(s: str) -> str:
    """SHA256 哈希后 Base64 URL 编码"""
    return _b64url_no_pad(hashlib.sha256(s.encode("ascii")).digest())

def generate_oauth_url():
    """生成 OAuth 授权 URL"""
    state = secrets.token_urlsafe(16)
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = _sha256_b64url_no_pad(code_verifier)
    
    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPE,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "prompt": "login",
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
    }
    
    auth_url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"
    return auth_url, state, code_verifier

class CallbackHandler(BaseHTTPRequestHandler):
    code = None
    state = None
    error = None
    event = threading.Event()
    
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        
        self.code = query.get("code", [None])[0]
        self.state = query.get("state", [None])[0]
        self.error = query.get("error", [None])[0]
        
        # 返回成功页面
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        
        if self.code:
            self.wfile.write(b"""
            <html>
            <head><title>登录成功</title></head>
            <body>
            <h1>✅ 登录成功！</h1>
            <p>正在提取 Token...</p>
            <p>您可以关闭此页面</p>
            </body>
            </html>
            """)
        else:
            self.wfile.write(f"<h1>错误：{self.error}</h1>".encode())
        
        self.event.set()
    
    def log_message(self, format, *args):
        pass  # 禁用日志

def exchange_code(code: str, code_verifier: str) -> dict:
    """用授权码交换 token"""
    import requests
    
    data = {
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "code_verifier": code_verifier,
    }
    
    response = requests.post(TOKEN_URL, data=data, timeout=30)
    if response.status_code == 200:
        return response.json()
    else:
        raise Exception(f"Token 交换失败：{response.status_code} - {response.text}")

def main():
    print("=" * 60)
    print("ChatGPT 手动登录获取 Token 工具")
    print("=" * 60)
    
    # 生成 OAuth URL
    auth_url, state, code_verifier = generate_oauth_url()
    
    print("\n请在浏览器中打开以下 URL 进行登录：")
    print("-" * 60)
    print(auth_url)
    print("-" * 60)
    
    # 启动回调服务器
    server = HTTPServer(("localhost", 1455), CallbackHandler)
    server.timeout = 300  # 5 分钟超时
    
    print("\n已在 localhost:1455 启动回调服务器")
    print("请在登录完成后等待自动提取 Token...")
    print("（按 Ctrl+C 取消）\n")
    
    # 等待回调
    try:
        server.handle_request()
        CallbackHandler.event.wait(timeout=10)
    except KeyboardInterrupt:
        print("\n已取消")
        server.server_close()
        return
    
    server.server_close()
    
    # 检查回调结果
    if CallbackHandler.error:
        print(f"❌ 登录失败：{CallbackHandler.error}")
        return
    
    if not CallbackHandler.code:
        print("❌ 未获取到授权码")
        return
    
    print(f"✅ 获取到授权码：{CallbackHandler.code[:20]}...")
    
    # 交换 token
    print("正在交换 Token...")
    try:
        token_data = exchange_code(CallbackHandler.code, code_verifier)
        
        print("\n" + "=" * 60)
        print("✅ 成功获取 Token！")
        print("=" * 60)
        
        # 解析 ID Token 获取账户信息
        id_token = token_data.get("id_token", "")
        if id_token:
            try:
                payload = id_token.split(".")[1]
                pad = "=" * ((4 - (len(payload) % 4)) % 4)
                decoded = base64.urlsafe_b64decode((payload + pad).encode("ascii"))
                claims = json.loads(decoded.decode("utf-8"))
                
                email = claims.get("email", "")
                auth = claims.get("https://api.openai.com/auth", {})
                account_id = auth.get("chatgpt_account_id", "")
                
                print(f"\n邮箱：{email}")
                print(f"Account ID: {account_id}")
            except Exception as e:
                print(f"解析 ID Token 失败：{e}")
        
        print(f"\nAccess Token: {token_data.get('access_token', '')[:50]}...")
        print(f"Refresh Token: {token_data.get('refresh_token', '')[:50]}...")
        print(f"ID Token: {token_data.get('id_token', '')[:50]}...")
        
        # 保存 token
        save = input("\n是否保存 Token 到配置文件？(y/n): ").strip().lower()
        if save == 'y':
            config = {
                "access_token": token_data.get("access_token"),
                "refresh_token": token_data.get("refresh_token"),
                "id_token": token_data.get("id_token"),
                "account_id": account_id if 'account_id' in dir() else "",
                "email": email if 'email' in dir() else "",
            }
            with open("chatgpt_tokens.json", "w") as f:
                json.dump(config, f, indent=2)
            print("✅ Token 已保存到 chatgpt_tokens.json")
        
    except Exception as e:
        print(f"❌ Token 交换失败：{e}")

if __name__ == "__main__":
    main()
