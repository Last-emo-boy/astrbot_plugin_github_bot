# main.py
import json
import asyncio
import aiohttp
from aiohttp import web
from astrbot.api.all import *

@register(
    "github", 
    "YourName", 
    "GitHub 相关功能插件，基于 GitHub API 实现 OAuth 授权、仓库查询以及 Webhook 事件处理",
    "1.0.0",
    "https://github.com/yourname/astrbot_plugin_github_bot"
)
class GitHubPlugin(Star):
    # 使用类变量确保 HTTP 服务器只启动一次
    _server_started = False

    def __init__(self, context: Context, config: dict):
        """
        初始化插件，同时从配置中读取 GitHub OAuth 的 appId、appSecret 及 webhook 推送目标频道。
        如果需要对外提供 HTTP 接口，则启动一个独立的 aiohttp HTTP 服务器，用于处理 OAuth 回调和 GitHub Webhook 推送。
        """
        super().__init__(context)
        self.config = config
        self.app_id = self.config.get("appId", "").strip()
        self.app_secret = self.config.get("appSecret", "").strip()
        self.webhook_channel = self.config.get("webhookChannel", "").strip()
        # 存储用户授权后的 access token，键为调用者的唯一 ID（通过 OAuth 的 state 参数传入）
        self.user_tokens = {}

        # HTTP 服务器配置：host 与 port 可在配置中指定，默认为 0.0.0.0:8080
        self.http_host = self.config.get("httpHost", "0.0.0.0")
        self.http_port = int(self.config.get("httpPort", 8080))
        if not GitHubPlugin._server_started:
            GitHubPlugin._server_started = True
            asyncio.create_task(self.start_http_server())

    async def start_http_server(self):
        """
        启动一个 aiohttp HTTP 服务器，注册两个路由：
          GET /github/authorize 用于处理 GitHub OAuth 回调；
          POST /github/webhook   用于处理 GitHub Webhook 推送。
        """
        app = web.Application()
        app.router.add_get("/github/authorize", self.oauth_callback_handler)
        app.router.add_post("/github/webhook", self.webhook_handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host=self.http_host, port=self.http_port)
        await site.start()
        # 记录日志（假设 context.logger 可用）
        if hasattr(self.context, "logger"):
            self.context.logger.info(f"GitHub Plugin HTTP server started on http://{self.http_host}:{self.http_port}")
        else:
            print(f"GitHub Plugin HTTP server started on http://{self.http_host}:{self.http_port}")

    async def oauth_callback_handler(self, request: web.Request):
        """
        处理 GitHub OAuth 回调请求，交换 code 为 access token。
        请求预期为 GET 方法，必须包含 code 与 state 参数（state 为调用者 ID）。
        """
        if request.method.upper() != "GET":
            return web.Response(text="不支持的请求方法", status=405)
        query = request.query
        code = query.get("code")
        state = query.get("state")  # 此处 state 为调用者 ID
        if not code or not state:
            return web.Response(text="缺少 code 或 state 参数", status=400)
        # 从全局配置中获取 selfUrl（确保该地址是机器人公网可访问的地址）
        self_url = self.context.config.get("selfUrl", "https://example.com").rstrip("/")
        callback_url = f"{self_url}/github/authorize"
        token_url = "https://github.com/login/oauth/access_token"
        data = {
            "client_id": self.app_id,
            "client_secret": self.app_secret,
            "code": code,
            "redirect_uri": callback_url,
            "state": state,
        }
        headers = {"Accept": "application/json"}
        async with aiohttp.ClientSession() as session:
            async with session.post(token_url, data=data, headers=headers) as resp:
                result = await resp.json()
        access_token = result.get("access_token")
        if access_token:
            # 保存 access token，键为调用者的 ID（state 参数）
            self.user_tokens[state] = access_token
            return web.Response(text="GitHub 授权成功，您现在可以使用 GitHub 相关指令。")
        else:
            err_msg = result.get("error_description", "未知错误")
            return web.Response(text=f"GitHub 授权失败：{err_msg}", status=400)

    async def webhook_handler(self, request: web.Request):
        """
        处理 GitHub Webhook 推送请求，预期为 POST 方法。
        解析请求中的 JSON payload 及请求头中的 X-GitHub-Event，并将格式化信息发送到配置的目标频道（webhookChannel）。
        """
        if request.method.upper() != "POST":
            return web.Response(text="不支持的请求方法", status=405)
        try:
            payload = await request.json()
        except Exception as e:
            return web.Response(text=f"解析 Webhook payload 失败：{str(e)}", status=400)
        event_type = request.headers.get("X-GitHub-Event", "unknown")
        message = (
            f"收到 GitHub Webhook 事件：{event_type}\n"
            f"Payload:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
        )
        if self.webhook_channel:
            await self.context.send_message(
                self.webhook_channel,
                MessageChain().message(message)
            )
        return web.Response(text="Webhook 接收成功")

    @command("github")
    async def github_help(self, event: AstrMessageEvent):
        """
        返回 GitHub 插件的帮助信息及指令列表。
        使用方法:
          /github
        """
        help_text = (
            "GitHub 插件指令列表：\n"
            "  /github.authorize - 获取 GitHub OAuth 授权链接\n"
            "  /github.repos     - 查看你的 GitHub 仓库列表\n\n"
            "请确保机器人已部署于公网，并在全局设置中配置 selfUrl。"
        )
        yield event.plain_result(help_text)

    @command("github.authorize")
    async def github_authorize(self, event: AstrMessageEvent):
        """
        生成 GitHub OAuth 授权链接。
        使用方法:
          /github.authorize
        说明：
          请先在 GitHub 中创建 OAuth App，填写 Client ID 与 Client Secret 至插件配置中；
          回调 URL 为 [selfUrl]/github/authorize，其中 selfUrl 来源于全局配置。
          此处附带调用者 ID（state）用于后续保存 access token。
        """
        user_id = event.get_sender_id()
        # 从全局配置中获取 selfUrl（注意去除末尾的斜杠）
        self_url = self.context.config.get("selfUrl", "https://example.com").rstrip("/")
        callback_url = f"{self_url}/github/authorize"
        auth_url = (
            f"https://github.com/login/oauth/authorize"
            f"?client_id={self.app_id}"
            f"&redirect_uri={callback_url}"
            f"&state={user_id}"
        )
        reply = f"请点击以下链接进行 GitHub 授权：\n{auth_url}"
        yield event.plain_result(reply)

    @command("github.repos")
    async def github_repos(self, event: AstrMessageEvent):
        """
        获取经过授权后的 GitHub 仓库列表。
        使用方法:
          /github.repos
        调用前请确保已使用 /github.authorize 完成 OAuth 授权。
        """
        user_id = event.get_sender_id()
        token = self.user_tokens.get(user_id)
        if not token:
            yield event.plain_result("未检测到授权信息，请先使用 /github.authorize 进行授权。")
            return
        headers = {"Authorization": f"token {token}", "Accept": "application/json"}
        api_url = "https://api.github.com/user/repos"
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, headers=headers) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    yield event.plain_result(f"获取仓库列表失败：{error_text}")
                    return
                repos = await resp.json()
        if not repos:
            yield event.plain_result("没有获取到仓库。")
            return
        repo_list = "\n".join([repo["full_name"] for repo in repos])
        yield event.plain_result("你的仓库：\n" + repo_list)
