# main.py
import json
import requests
from astrbot.api.all import *
from astrbot.api.message_components import MessageChain

@register(
    "github", 
    "YourName", 
    "GitHub 相关功能插件，基于 GitHub API 实现 OAuth 授权、仓库查询以及 Webhook 事件处理",
    "1.0.0",
    "https://github.com/yourname/astrbot_plugin_github"
)
class GitHubPlugin(Star):
    def __init__(self, context: Context, config: dict):
        """
        初始化插件，同时从配置中读取 GitHub OAuth 的 appId、appSecret 及 webhook 推送目标频道。
        """
        super().__init__(context)
        self.config = config
        self.app_id = self.config.get("appId", "").strip()
        self.app_secret = self.config.get("appSecret", "").strip()
        self.webhook_channel = self.config.get("webhookChannel", "").strip()
        # 存储用户授权后的 access token，键为调用者的唯一 ID（通过 OAuth 的 state 参数传入）
        self.user_tokens = {}

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
        # 从全局配置中获取 selfUrl（默认为示例 URL）
        self_url = self.context.config.get("selfUrl", "https://example.com").rstrip("/")
        # 默认回调路径为 /github/authorize
        callback_url = f"{self_url}/github/authorize"
        # 在授权链接中附带 state 参数（调用者唯一标识）
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
        # 调用 GitHub API 获取当前用户的仓库列表
        headers = {"Authorization": f"token {token}", "Accept": "application/json"}
        api_url = "https://api.github.com/user/repos"
        response = requests.get(api_url, headers=headers)
        if response.status_code != 200:
            yield event.plain_result(f"获取仓库列表失败：{response.text}")
            return
        repos = response.json()
        if not repos:
            yield event.plain_result("没有获取到仓库。")
            return
        repo_list = "\n".join([repo["full_name"] for repo in repos])
        yield event.plain_result("你的仓库：\n" + repo_list)

    @filter.on_event("http_request")
    async def handle_http_request(self, event: AstrMessageEvent):
        """
        处理 HTTP 请求，分为两类：
          1. GET 请求 /github/authorize：处理 GitHub OAuth 回调。
          2. POST 请求 /github/webhook   ：处理 GitHub Webhook 推送。
        """
        # 假设 event 对象包含以下属性：
        #   event.request_path: 请求路径
        #   event.http_method: HTTP 方法 ("GET" 或 "POST")
        #   event.request: 请求对象，支持 query（字典）、headers（字典）和异步 json() 方法
        if event.request_path == "/github/authorize" and event.http_method.upper() == "GET":
            # 处理 OAuth 回调
            query = event.request.query  # 字典形式，包含 code 与 state
            code = query.get("code")
            state = query.get("state")  # 此处 state 为调用者 ID
            if not code or not state:
                return event.respond("缺少 code 或 state 参数")
            self_url = self.context.config.get("selfUrl", "https://example.com").rstrip("/")
            callback_url = f"{self_url}/github/authorize"
            # 交换 code 为 access token
            token_url = "https://github.com/login/oauth/access_token"
            data = {
                "client_id": self.app_id,
                "client_secret": self.app_secret,
                "code": code,
                "redirect_uri": callback_url,
                "state": state,
            }
            headers = {"Accept": "application/json"}
            try:
                resp = requests.post(token_url, data=data, headers=headers)
                result = resp.json()
            except Exception as e:
                return event.respond(f"请求 GitHub 失败：{str(e)}")
            access_token = result.get("access_token")
            if access_token:
                # 保存 access token，键为调用者的 ID（state 参数）
                self.user_tokens[state] = access_token
                return event.respond("GitHub 授权成功，您现在可以使用 GitHub 相关指令。")
            else:
                err_msg = result.get("error_description", "未知错误")
                return event.respond(f"GitHub 授权失败：{err_msg}")
        elif event.request_path == "/github/webhook" and event.http_method.upper() == "POST":
            # 处理 GitHub Webhook 推送
            try:
                payload = await event.request.json()
            except Exception as e:
                return event.respond(f"解析 Webhook payload 失败：{str(e)}")
            # 从请求头中获取 GitHub 事件类型
            event_type = event.request.headers.get("X-GitHub-Event", "unknown")
            # 格式化消息，实际情况可根据不同事件类型做更详细处理
            message = (
                f"收到 GitHub Webhook 事件：{event_type}\n"
                f"Payload:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
            )
            # 如果配置中设置了 webhookChannel，则发送消息到该频道
            if self.webhook_channel:
                await self.context.send_message(
                    self.webhook_channel,
                    MessageChain().message(message)
                )
            return event.respond("Webhook 接收成功")
        else:
            # 未处理的 HTTP 请求不做响应
            return None
