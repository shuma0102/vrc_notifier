from fastapi import FastAPI
from fastapi_utils.tasks import repeat_every
import os
from dotenv import load_dotenv
import httpx
import pyotp
import asyncio

load_dotenv()

app = FastAPI()

USERNAME = os.getenv("VRC_USERNAME")
PASSWORD = os.getenv("VRC_PASSWORD")
TOTP_SECRET = os.getenv("VRC_TOTP_SECRET")
GROUP_ID = os.getenv("VRC_GROUP_ID")
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

last_instance_id = None
client: httpx.AsyncClient = None  # グローバルクライアント


# VRChat ログインとTOTP認証
async def login_vrchat(max_retries: int = 5) -> httpx.AsyncClient:
    session = httpx.AsyncClient()
    for attempt in range(max_retries):
        res = await session.get(
            "https://api.vrchat.cloud/api/1/auth/user",
            auth=(USERNAME, PASSWORD)
        )

        print("Login status code:", res.status_code)
        print("Login response:", res.text)

        if res.status_code == 200:
            json_data = res.json()
            if "requiresTwoFactorAuth" in json_data:
                print("🔐 TOTPが必要なアカウントです。認証開始…")

                totp = pyotp.TOTP(TOTP_SECRET)
                code = totp.now()

                verify = await session.post(
                    "https://api.vrchat.cloud/api/1/auth/twofactorauth/totp/verify",
                    json={"code": code}
                )

                print("TOTP verify status:", verify.status_code)
                print("TOTP verify response:", verify.text)

                if verify.status_code == 200:
                    print("✅ 2FA認証成功！")
                    return session
                elif verify.status_code == 429:
                    wait_time = 2 ** attempt
                    print(f"⏳ レート制限、{wait_time}秒待機して再試行 ({attempt + 1}/{max_retries})")
                    await asyncio.sleep(wait_time)
                else:
                    break
            else:
                print("✅ 2FA不要のアカウント、ログイン完了")
                return session
        else:
            break

    raise Exception("❌ 2FA認証失敗またはログイン不能（リトライ上限）")



# グループのインスタンス取得
async def get_group_instances():
    global client
    instance_url = f"https://api.vrchat.cloud/api/1/groups/{GROUP_ID}/instances"
    res = await client.get(instance_url)

    print("Instance status code:", res.status_code)
    print("Instance response:", res.text)

    if res.status_code == 200:
        return res.json()
    else:
        error_detail = res.json().get("error", {}).get("message", "Unknown error")
        raise Exception(f"インスタンス取得失敗: {error_detail}")


# Discord通知（Embed形式）
async def notify_discord(instance):
    if not DISCORD_WEBHOOK_URL:
        print("webhook URL未設定")
        return

    world = instance.get("world", {})
    world_name = world.get("name", "不明なワールド")
    members = instance.get("memberCount", "?")
    location = instance.get("location", "不明")
    world_id = world.get("id", "")
    instance_id = instance.get("instanceId", "")
    image_url = world.get("imageUrl", "")

    launch_url = f"https://vrchat.com/home/launch?worldId={world_id}&{instance_id}"

    embed = {
        "title": "🆕 新しいグループインスタンスが立ったよ！",
        "description": f"**ワールド名:** {world_name}\n**人数:** {members}人\n[VRChatで開く]({launch_url})",
        "color": 0x00bfff,
        "image": {"url": image_url},
        "footer": {"text": location}
    }

    async with httpx.AsyncClient() as local_client:
        await local_client.post(DISCORD_WEBHOOK_URL, json={"embeds": [embed]})


# 起動時にログインセッション確保
@app.on_event("startup")
async def startup_event():
    global client
    client = await login_vrchat()


@app.on_event("shutdown")
async def shutdown_event():
    global client
    if client:
        await client.aclose()


# 定期チェック
@app.on_event("startup")
@repeat_every(seconds=60)
async def check_for_instances():
    global last_instance_id
    try:
        instances = await get_group_instances()
        if not instances:
            return

        latest = instances[0]
        current_id = latest["instanceId"]

        if current_id != last_instance_id:
            await notify_discord(latest)
            last_instance_id = current_id
            print("✅ Discord通知を送信しました")
        else:
            print("📡 インスタンスに変化なし")

    except Exception as e:
        print("❌ チェックエラー:", e)


# 手動でインスタンス一覧取得
@app.get("/instances")
async def list_instances():
    try:
        instances = await get_group_instances()
        return {"instances": instances}
    except Exception as e:
        return {"error": str(e)}
