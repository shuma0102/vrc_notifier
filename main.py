# --------------------------------------------------------
# vrc_notifier: VRChatグループのインスタンスが立ったらDiscordに通知するFastAPIアプリ
# --------------------------------------------------------

from fastapi import FastAPI
from fastapi_utils.tasks import repeat_every  # 定期実行用
import os
from dotenv import load_dotenv
import httpx  # 非同期HTTPクライアント
import pyotp  # TOTP認証コード生成（2段階認証用）

# .envファイルから環境変数を読み込む
load_dotenv()

# FastAPIアプリの初期化
app = FastAPI()

# --------------------------------------------------------
# 環境変数（.envから取得）
# --------------------------------------------------------
USERNAME = os.getenv("VRC_USERNAME")  # VRChatログインID（メールアドレス）
PASSWORD = os.getenv("VRC_PASSWORD")  # パスワード
TOTP_SECRET = os.getenv("VRC_TOTP_SECRET")  # TOTPのシークレット（2FA用）
GROUP_ID = os.getenv("VRC_GROUP_ID")  # 通知対象のVRChatグループID
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")  # 通知先DiscordのWebhook URL

# 最後に通知したインスタンスIDを保存
last_instance_id = None


# --------------------------------------------------------
# VRChatにログイン（2FA対応、リトライ機能あり）
# --------------------------------------------------------
async def login_vrchat(max_retries: int = 5):
    for attempt in range(max_retries):
        client = httpx.AsyncClient()
        res = await client.get(
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

                verify = await client.post(
                    "https://api.vrchat.cloud/api/1/auth/twofactorauth/totp/verify",
                    json={"code": code}
                )

                print("TOTP verify status:", verify.status_code)
                print("TOTP verify response:", verify.text)

                if verify.status_code == 200:
                    print("✅ 2FA認証成功！")
                    return client
                elif verify.status_code == 429:
                    wait_time = 2 ** attempt
                    print(f"⏳ レート制限、{wait_time}秒待機して再試行 ({attempt + 1}/{max_retries})")
                    await client.aclose()
                    await asyncio.sleep(wait_time)
                    continue
                else:
                    await client.aclose()
                    break
            else:
                print("✅ 2FA不要のアカウント、ログイン完了")
                return client
        else:
            await client.aclose()
            break

    raise Exception("❌ 2FA認証失敗またはログイン不能（リトライ上限）")


# --------------------------------------------------------
# Discordに通知を送信（Embed形式）
# --------------------------------------------------------
async def notify_discord(instance):
    if not DISCORD_WEBHOOK_URL:
        print("❌ Webhook URLが設定されていません")
        return

    world = instance.get("world", {})
    embed = {
        "title": "🎉 新しいグループインスタンスが立ったよ！",
        "description": f"**ワールド名:** {world.get('name', '不明')}\n"
                       f"**人数:** {instance.get('memberCount', '?')}人\n"
                       f"**Location:** `{instance.get('location', '不明')}`\n"
                       f"[VRChatで開く](https://vrchat.com/home/launch?worldId={world.get('id')}&instanceId={instance.get('instanceId')})",
        "thumbnail": {"url": world.get("thumbnailImageUrl", "")},
        "color": 0x00BFFF  # 水色
    }

    async with httpx.AsyncClient() as client:
        await client.post(DISCORD_WEBHOOK_URL, json={"embeds": [embed]})


# --------------------------------------------------------
# グループのインスタンス一覧を取得（最新順）
# --------------------------------------------------------
async def get_group_instances():
    client = await login_vrchat()
    instance_url = f"https://api.vrchat.cloud/api/1/groups/{GROUP_ID}/instances"
    res = await client.get(instance_url)
    await client.aclose()

    print("Instance status code:", res.status_code)
    print("Instance response:", res.text)

    if res.status_code == 200:
        return res.json()
    else:
        error_detail = res.json().get("error", {}).get("message", "Unknown error")
        raise Exception(f"インスタンス取得失敗: {error_detail}")


# --------------------------------------------------------
# 起動時に定期実行を登録（60秒ごとにチェック）
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
# 手動でインスタンスを確認するAPIエンドポイント
# --------------------------------------------------------
@app.get("/instances")
async def list_instances():
    try:
        instances = await get_group_instances()
        return {"instances": instances}
    except Exception as e:
        return {"error": str(e)}
