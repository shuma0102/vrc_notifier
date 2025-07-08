# vrc_notifier: VRChatグループのインスタンスが立ったらDiscordに通知するFastAPIアプリ

from fastapi import FastAPI
from fastapi_utils.tasks import repeat_every
import os
from dotenv import load_dotenv
import httpx
import pyotp
import asyncio
import random
import colorsys

load_dotenv()
app = FastAPI()

USERNAME = os.getenv("VRC_USERNAME")
PASSWORD = os.getenv("VRC_PASSWORD")
TOTP_SECRET = os.getenv("VRC_TOTP_SECRET")
GROUP_ID = os.getenv("VRC_GROUP_ID")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

last_instance_id = None
vrc_client: httpx.AsyncClient | None = None  # セッション再利用用

# --------------------------------------------------------
# VRChatログインセッションの取得
# --------------------------------------------------------
async def login_vrchat(max_retries: int = 5) -> httpx.AsyncClient:
    global vrc_client

    if vrc_client:
        return vrc_client  # 再利用

    for attempt in range(max_retries):
        client = httpx.AsyncClient()
        res = await client.get(
            "https://api.vrchat.cloud/api/1/auth/user",
            auth=(USERNAME, PASSWORD)
        )

        print("Login status code:", res.status_code)
        print("Login response:", res.text)

        if res.status_code == 200:
            data = res.json()
            if "requiresTwoFactorAuth" in data:
                print("🔐 TOTPが必要なアカウントです。認証開始…")
                code = pyotp.TOTP(TOTP_SECRET).now()
                verify = await client.post(
                    "https://api.vrchat.cloud/api/1/auth/twofactorauth/totp/verify",
                    json={"code": code}
                )
                print("TOTP verify status:", verify.status_code)
                print("TOTP verify response:", verify.text)

                if verify.status_code == 200:
                    print("✅ 2FA認証成功！")
                    vrc_client = client
                    return client
                elif verify.status_code == 429:
                    wait_time = 2 ** attempt
                    print(f"⏳ レート制限、{wait_time}秒待機して再試行 ({attempt + 1}/{max_retries})")
                    await client.aclose()
                    await asyncio.sleep(wait_time)
                    continue
            else:
                print("✅ 2FA不要のアカウント、ログイン完了")
                vrc_client = client
                return client

        await client.aclose()
        break

    raise Exception("❌ 2FA認証失敗またはログイン不能（リトライ上限）")

# --------------------------------------------------------
# インスタンス一覧取得（セッション失効時は再ログイン）
# --------------------------------------------------------
async def get_group_instances():
    global vrc_client
    try:
        client = await login_vrchat()
        instance_url = f"https://api.vrchat.cloud/api/1/groups/{GROUP_ID}/instances"
        res = await client.get(instance_url)

        if res.status_code == 401:
            print("⚠ セッション期限切れ。再ログイン中…")
            vrc_client = None
            return await get_group_instances()

        print("Instance status code:", res.status_code)
        print("Instance response:", res.text)

        if res.status_code == 200:
            return res.json()
        else:
            detail = res.json().get("error", {}).get("message", "Unknown error")
            raise Exception(f"インスタンス取得失敗: {detail}")
    except Exception as e:
        raise e

# --------------------------------------------------------
# Discord通知（Embed・パステルカラー）
# --------------------------------------------------------
async def notify_discord(instance):
    if not DISCORD_WEBHOOK_URL:
        print("❌ Webhook URLが設定されていません")
        return

    world = instance.get("world", {})
    h = random.random()
    s = 0.4
    l = 0.8
    r, g, b = colorsys.hls_to_rgb(h, l, s)
    pastel_color = (int(r * 255) << 16) + (int(g * 255) << 8) + int(b * 255)

    embed = {
        "title": "🎉 新しいグループインスタンスが立ったよ！",
        "description": f"**ワールド名:** {world.get('name', '不明')}\n"
                       f"**人数:** {instance.get('memberCount', '?')}人\n"
                       f"**Location:** `{instance.get('location', '不明')}`\n"
                       f"[VRChatで開く](https://vrchat.com/home/launch?worldId={world.get('id')}&instanceId={instance.get('instanceId')})",
        "thumbnail": {"url": world.get("thumbnailImageUrl", "")},
        "color": pastel_color
    }

    async with httpx.AsyncClient() as client:
        await client.post(DISCORD_WEBHOOK_URL, json={"embeds": [embed]})

# --------------------------------------------------------
# 起動時の定期監視処理（60秒おき）
# --------------------------------------------------------
@app.on_event("startup")
@repeat_every(seconds=60)
async def startup_event():
    global last_instance_id
    try:
        instances = await get_group_instances()
        if not instances:
            print("⚠ インスタンスなし")
            return

        latest = instances[0]
        current_id = latest["instanceId"]

        if current_id != last_instance_id:
            await notify_discord(latest)
            last_instance_id = current_id
            print("✅ Discordに通知を送信しました")
        else:
            print("🔁 インスタンスに変化なし")
    except Exception as e:
        print("❌ チェック中にエラー発生:", e)

# --------------------------------------------------------
# インスタンス確認API
# --------------------------------------------------------
@app.get("/instances")
async def list_instances():
    try:
        return {"instances": await get_group_instances()}
    except Exception as e:
        return {"error": str(e)}

# --------------------------------------------------------
# テスト通知API（インスタンスがないとき手動送信）
# --------------------------------------------------------
@app.get("/test-notification")
async def test_notification():
    fake_instance = {
        "instanceId": "test123",
        "location": "wrld_test:test123",
        "memberCount": random.randint(1, 10),
        "world": {
            "name": "テストワールド",
            "id": "wrld_test",
            "authorName": "tester",
            "thumbnailImageUrl": "https://upload.wikimedia.org/wikipedia/commons/thumb/e/ec/VR_icon.svg/1024px-VR_icon.svg.png"
        }
    }
    await notify_discord(fake_instance)
    return {"message": "テストメッセージを送信しました"}
