"""
AnimeVerse Upload Bot — Railway + VOE Edition
=============================================
Flow:
  1. /setup anime-id S1
  2. Saari files forward karo
  3. Bot har file ka Telegram streaming link banayega
  4. Wo link VOE ko dega — VOE khud download karega
  5. Firebase mein save hoga
  6. /done
"""

import re
import os
import asyncio
import requests
import firebase_admin
from firebase_admin import credentials, db
from pyrogram import Client, filters
from pyrogram.types import Message
from aiohttp import web
import aiohttp

# ══════════════════════════════════════════════════════
#   SETTINGS — Railway Environment Variables se aayega
# ══════════════════════════════════════════════════════

API_ID           = int(os.environ.get("API_ID", "36024010"))
API_HASH         = os.environ.get("API_HASH", "cd827b4e9ecd8604de7ecd99655c603c")
BOT_TOKEN        = os.environ.get("BOT_TOKEN", "")
ALLOWED_USER     = int(os.environ.get("ALLOWED_USER", "7373324949"))
VOE_API_KEY      = os.environ.get("VOE_API_KEY", "")
FIREBASE_URL     = os.environ.get("FIREBASE_URL", "https://animeverse-9eada-default-rtdb.firebaseio.com/")
RAILWAY_URL      = os.environ.get("RAILWAY_STATIC_URL", "")  # Railway public URL

QUALITIES_PER_EP = 3

# ══════════════════════════════════════════════════════
#   FIREBASE INIT
# ══════════════════════════════════════════════════════

cred = credentials.Certificate("key.json")
firebase_admin.initialize_app(cred, {"databaseURL": FIREBASE_URL})

# ══════════════════════════════════════════════════════
#   PYROGRAM CLIENT
# ══════════════════════════════════════════════════════

app = Client(
    "animeverse_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

# ══════════════════════════════════════════════════════
#   STATE
# ══════════════════════════════════════════════════════

session = {
    "anime_id" : None,
    "season"   : None,
    "done_eps" : 0,
}
ep_buffer = {}

def reset_all():
    session.update({"anime_id": None, "season": None, "done_eps": 0})
    ep_buffer.clear()

# ══════════════════════════════════════════════════════
#   CAPTION PARSER
# ══════════════════════════════════════════════════════

def parse_caption(text: str):
    if not text:
        return None, None
    t = text.upper()
    ep_num = None

    ep_match = re.search(r'\bEP(?:ISODE)?\s*[-:→►\s]*\s*(\d{1,3})\b', t)
    if ep_match:
        ep_num = int(ep_match.group(1))

    if not ep_num:
        e_match = re.search(r'\bE(\d{1,3})\b', t)
        if e_match:
            ep_num = int(e_match.group(1))

    if not ep_num:
        cleaned = re.sub(r'\bS\d{1,2}\b', '', t)
        nums = re.findall(r'\b(\d{1,2})\b', cleaned)
        if nums:
            ep_num = int(nums[0])

    quality = None
    q_match = re.search(r'\b(1080P|720P|480P)\b', t)
    if q_match:
        quality = q_match.group(1).replace("P", "p")

    return ep_num, quality

# ══════════════════════════════════════════════════════
#   TELEGRAM DIRECT URL — bina download ke!
# ══════════════════════════════════════════════════════

def get_tg_url(file_id: str) -> str | None:
    """
    file_id se direct Telegram URL nikalo.
    50MB tak: getFile API se
    50MB se upar: Pyrogram se file path nikalte hain
    """
    try:
        res = requests.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/getFile",
            params={"file_id": file_id},
            timeout=30
        )
        data = res.json()
        if data.get("ok"):
            path = data["result"]["file_path"]
            return f"https://api.telegram.org/file/bot{BOT_TOKEN}/{path}"
    except Exception as e:
        print(f"  ❌ TG URL error: {e}")
    return None

# ══════════════════════════════════════════════════════
#   VOE REMOTE UPLOAD — Telegram URL se seedha!
# ══════════════════════════════════════════════════════

def voe_remote_upload(tg_url: str, file_name: str) -> str | None:
    """
    Telegram URL de do — VOE khud download karke upload karega.
    Tera koi data nahi lagega!
    """
    try:
        # VOE URL upload API
        res = requests.get(
            "https://voe.sx/api/upload/url",
            params={
                "key" : VOE_API_KEY,
                "url" : tg_url,
                "name": file_name,
            },
            timeout=120
        )
        data = res.json()
        print(f"  📥 VOE Response: {data}")

        if data.get("status") == 200:
            result = data.get("result", [{}])
            if isinstance(result, list) and result:
                code = result[0].get("code") or result[0].get("filecode")
            elif isinstance(result, dict):
                code = result.get("code") or result.get("filecode")
            else:
                code = None

            if code:
                return f"https://voe.sx/e/{code}"

        print(f"  ❌ VOE upload failed: {data}")

    except Exception as e:
        print(f"  ❌ VOE error: {e}")

    return None

# ══════════════════════════════════════════════════════
#   FIREBASE SAVE
# ══════════════════════════════════════════════════════

def save_to_firebase(anime_id, season, ep_num, quality_dict):
    ep_key = f"E{str(ep_num).zfill(2)}"
    if not quality_dict:
        return ep_key
    db.reference(f"anime_links/{anime_id}/{season}/{ep_key}").update(quality_dict)
    print(f"  ✅ Firebase saved: {ep_key}")
    return ep_key

# ══════════════════════════════════════════════════════
#   PROCESS EPISODE
# ══════════════════════════════════════════════════════

async def process_ep(client: Client, chat_id: int, ep_num: int, files: list):
    anime_id = session["anime_id"]
    season   = session["season"]
    ep_key   = f"E{str(ep_num).zfill(2)}"

    sorted_files = sorted(files, key=lambda x: x["size"])
    quality_map  = {0: "480p", 1: "720p", 2: "1080p"}
    for i, f in enumerate(sorted_files):
        f["quality"] = quality_map.get(i, f"part{i+1}")

    await client.send_message(
        chat_id,
        f"⚙️ **{ep_key} — VOE Upload Shuru...**\n"
        f"VOE khud download karega — tera data nahi lagega! ⚡"
    )

    quality_dict = {}

    for f in sorted_files:
        quality  = f["quality"]
        size_mb  = round(f["size"] / (1024 * 1024), 1)
        fname    = f.get("name") or f"{anime_id}_{season}_{ep_key}_{quality}.mp4"

        await client.send_message(
            chat_id,
            f"🔗 `{quality}` ({size_mb}MB) — Telegram link nikal raha hoon..."
        )

        # Step 1: Telegram URL nikalo (instant!)
        tg_url = get_tg_url(f["file_id"])

        if not tg_url:
            await client.send_message(
                chat_id,
                f"❌ `{quality}` — Telegram URL nahi mila!\n"
                f"File 50MB se badi hai, Railway pe Pyrogram download hoga..."
            )
            # Fallback: Pyrogram se download karke VOE pe upload
            voe_link = await pyrogram_fallback(client, f, fname, quality)
        else:
            await client.send_message(
                chat_id,
                f"📡 `{quality}` — VOE ko link de raha hoon, VOE download karega..."
            )
            # Step 2: VOE ko URL do — VOE khud download karega!
            voe_link = await asyncio.get_event_loop().run_in_executor(
                None, voe_remote_upload, tg_url, fname
            )

        if voe_link:
            quality_dict[quality] = voe_link
            await client.send_message(
                chat_id,
                f"✅ `{quality}` done!\n`{voe_link}`"
            )
        else:
            await client.send_message(
                chat_id,
                f"❌ `{quality}` fail!\nVOE key check karo."
            )

        await asyncio.sleep(1)

    saved_key = save_to_firebase(anime_id, season, ep_num, quality_dict)
    session["done_eps"] += 1

    if quality_dict:
        q_lines = "\n".join([f"  • {q}: ✅" for q in quality_dict])
        await client.send_message(
            chat_id,
            f"🎉 **{saved_key} Complete!**\n{q_lines}\n"
            f"🔗 `anime_links/{anime_id}/{season}/{saved_key}`"
        )
    else:
        await client.send_message(chat_id, f"❌ **{saved_key} Fail!**")

# ══════════════════════════════════════════════════════
#   PYROGRAM FALLBACK — 50MB+ files ke liye
#   Railway pe download hoga, tera phone free rahega!
# ══════════════════════════════════════════════════════

async def pyrogram_fallback(client, f, fname, quality):
    """50MB se badi files ke liye Railway pe download → VOE upload."""
    import tempfile
    tmp_path = None
    try:
        tmp_dir  = tempfile.mkdtemp()
        tmp_path = os.path.join(tmp_dir, fname)

        await client.download_media(
            message=f["message_obj"],
            file_name=tmp_path
        )

        # VOE pe upload
        def upload():
            try:
                server_res = requests.get(
                    "https://voe.sx/api/upload/server",
                    params={"key": VOE_API_KEY},
                    timeout=30
                )
                upload_url = server_res.json()["result"]
                with open(tmp_path, "rb") as fh:
                    res = requests.post(
                        upload_url,
                        params={"key": VOE_API_KEY},
                        files={"file": (fname, fh, "video/mp4")},
                        timeout=3600
                    )
                data = res.json()
                if data.get("status") == 200:
                    code = data["result"][0].get("code") or data["result"][0].get("filecode")
                    return f"https://voe.sx/e/{code}" if code else None
            except Exception as e:
                print(f"  ❌ Fallback upload error: {e}")
            return None

        return await asyncio.get_event_loop().run_in_executor(None, upload)

    except Exception as e:
        print(f"  ❌ Pyrogram fallback error: {e}")
        return None
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)

# ══════════════════════════════════════════════════════
#   COMMANDS
# ══════════════════════════════════════════════════════

@app.on_message(filters.command("start") & filters.user(ALLOWED_USER))
async def cmd_start(client, msg: Message):
    await msg.reply(
        "🎌 **AnimeVerse Upload Bot — Railway Edition**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "**Step 1:** `/setup anime-id S1`\n"
        "**Step 2:** Saari files forward karo\n"
        "**Step 3:** `/done`\n\n"
        "⚡ VOE khud download karega — tera data nahi lagega!\n\n"
        "📋 `/status` | 🔄 `/reset` | 🔍 `/check anime-id S1 5`"
    )

@app.on_message(filters.command("setup") & filters.user(ALLOWED_USER))
async def cmd_setup(client, msg: Message):
    try:
        parts = msg.text.split()
        reset_all()
        session["anime_id"] = parts[1]
        session["season"]   = parts[2].upper()
        await msg.reply(
            f"✅ **Setup Done!**\n"
            f"📺 Anime: `{parts[1]}`\n"
            f"🎬 Season: `{parts[2].upper()}`\n\n"
            f"Ab saari files forward karo! 🚀"
        )
    except:
        await msg.reply("❌ Format: `/setup anime-id S1`")

@app.on_message(filters.command("done") & filters.user(ALLOWED_USER))
async def cmd_done(client, msg: Message):
    pending = list(ep_buffer.keys())
    if pending:
        await msg.reply(f"⚙️ **{len(pending)} episodes process ho rahe hain...**")
        for ep_num in sorted(pending):
            files = ep_buffer.pop(ep_num)
            await process_ep(client, msg.chat.id, ep_num, files)

    await client.send_message(
        msg.chat.id,
        f"🏁 **Sab Complete!**\n"
        f"✅ `{session['done_eps']} episodes` Firebase mein!\n"
        f"📺 `{session['anime_id']}` | `{session['season']}`"
    )

@app.on_message(filters.command("status") & filters.user(ALLOWED_USER))
async def cmd_status(client, msg: Message):
    if not session["anime_id"]:
        await msg.reply("ℹ️ Koi session nahi. `/setup anime-id S1` karo.")
        return
    lines = [
        f"📋 **Status:**",
        f"📺 `{session['anime_id']}` | `{session['season']}`",
        f"✅ Done: `{session['done_eps']} eps`",
        f"⏳ Buffer: `{len(ep_buffer)} eps`"
    ]
    for ep_num in sorted(ep_buffer.keys()):
        c = len(ep_buffer[ep_num])
        lines.append(f"  E{str(ep_num).zfill(2)}: {c}/{QUALITIES_PER_EP} files")
    await msg.reply("\n".join(lines))

@app.on_message(filters.command("reset") & filters.user(ALLOWED_USER))
async def cmd_reset(client, msg: Message):
    reset_all()
    await msg.reply("🔄 **Reset done!**")

@app.on_message(filters.command("check") & filters.user(ALLOWED_USER))
async def cmd_check(client, msg: Message):
    try:
        parts  = msg.text.split()
        a_id   = parts[1]
        season = parts[2].upper()
        ep_num = str(parts[3]).zfill(2)
        data   = db.reference(f"anime_links/{a_id}/{season}/E{ep_num}").get()
        if data:
            lines = [f"📊 **{a_id} | {season} | E{ep_num}**"]
            for q, link in data.items():
                lines.append(f"• {q}: `{str(link)[:50]}`")
            await msg.reply("\n".join(lines))
        else:
            await msg.reply(f"❌ E{ep_num} nahi mila")
    except:
        await msg.reply("❌ Format: `/check anime-id S1 5`")

# ══════════════════════════════════════════════════════
#   FILE HANDLER
# ══════════════════════════════════════════════════════

@app.on_message(filters.user(ALLOWED_USER) & (filters.document | filters.video))
async def handle_file(client, msg: Message):
    if not session["anime_id"]:
        await msg.reply("❌ Pehle `/setup anime-id S1` karo!")
        return

    file_obj  = msg.document or msg.video
    file_id   = file_obj.file_id
    file_size = file_obj.file_size or 0
    file_name = getattr(file_obj, "file_name", None) or "video.mp4"
    caption   = msg.caption or ""

    ep_num, _ = parse_caption(caption)
    if not ep_num:
        ep_num, _ = parse_caption(file_name)

    if not ep_num:
        await msg.reply(
            f"⚠️ **Episode detect nahi hua!**\n"
            f"Caption mein `Episode - 04` ya `E04` likhna chahiye."
        )
        return

    if ep_num not in ep_buffer:
        ep_buffer[ep_num] = []

    existing_sizes = [f["size"] for f in ep_buffer[ep_num]]
    if file_size in existing_sizes:
        await msg.reply(f"⚠️ Same file dobara aai! Skip.")
        return

    ep_buffer[ep_num].append({
        "chat_id"     : msg.chat.id,
        "msg_id"      : msg.id,
        "file_id"     : file_id,
        "size"        : file_size,
        "quality"     : "pending",
        "name"        : file_name,
        "message_obj" : msg,
    })

    count   = len(ep_buffer[ep_num])
    size_mb = round(file_size / (1024 * 1024), 1)
    ep_key  = f"E{str(ep_num).zfill(2)}"

    if count >= QUALITIES_PER_EP:
        await msg.reply(
            f"📥 `{size_mb}MB` received\n"
            f"⚙️ **{ep_key} — Teeno files mil gayi! Upload shuru...**"
        )
        files = ep_buffer.pop(ep_num)
        await process_ep(client, msg.chat.id, ep_num, files)
    else:
        remaining = QUALITIES_PER_EP - count
        await msg.reply(
            f"📥 `{size_mb}MB` received\n"
            f"📦 **{ep_key}:** {count}/{QUALITIES_PER_EP} | aur **{remaining}** chahiye"
        )

# ══════════════════════════════════════════════════════
#   RUN
# ══════════════════════════════════════════════════════

print("=" * 50)
print("  🤖 AnimeVerse Bot — Railway + VOE Edition")
print("=" * 50)

app.run()
