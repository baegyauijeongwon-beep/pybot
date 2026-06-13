import re
import time
import os
import random
import requests
import logging
from dotenv import load_dotenv
from mastodon import Mastodon
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from bs4 import BeautifulSoup

# ===================== 환경 =====================
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE_DIR, ".env"))

MASTODON_SERVER = "https://by-of-garden.xyz"
ACCESS_TOKEN = os.getenv("MASTODON_ACCESS_TOKEN")
JSON_FILE = os.path.join(BASE_DIR, "store-bot.json")
SHEET_URL = os.getenv("GOOGLE_SHEET_URL")

INITIAL_MONEY = 0
SINCE_ID_FILE = "store_last_notification.txt"

# ===================== 로그 =====================
LOG_FILE = os.path.join(BASE_DIR, "storebot_image.log")
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

def log(msg):
    print(msg)
    logging.info(msg)

# ===================== Mastodon =====================
mastodon = Mastodon(
    access_token=ACCESS_TOKEN,
    api_base_url=MASTODON_SERVER
)

acct = mastodon.account_verify_credentials()
print("로그인:", acct["acct"])

# ===================== Sheets =====================
def get_sheets():
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = ServiceAccountCredentials.from_json_keyfile_name(JSON_FILE, scope)
    client = gspread.authorize(creds)
    doc = client.open_by_url(SHEET_URL)
    return doc.worksheet("상점"), doc.worksheet("명단"), doc.worksheet("랜덤풀")

# ===================== Utils =====================
def clean_html(html):
    return BeautifulSoup(html, "html.parser").get_text()

def safe_int(v):
    if not v or str(v).strip() == "":
        return 0
    return int(str(v).replace(",", "").strip())

def parse_inventory(s):
    if not s:
        return {}
    items = [i.strip() for i in s.split("｜") if i.strip()]
    d = {}
    for item in items:
        m = re.match(r"^(.+?)\[(\d+)\]$", item)
        if m:
            d[m.group(1).strip()] = int(m.group(2))
        else:
            d[item] = 1
    return d

def rebuild_inventory(d):
    return " ｜ ".join(
        f"{k}[{v}]" if v > 1 else k
        for k, v in d.items() if v > 0
    )

def load_since_id():
    if os.path.exists(SINCE_ID_FILE):
        with open(SINCE_ID_FILE) as f:
            return int(f.read().strip())
    return None

def save_since_id(i):
    with open(SINCE_ID_FILE, "w") as f:
        f.write(str(i))

# ===================== IMAGE SYSTEM =====================

def download_image(url):
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200 and r.content:
            return r.content
    except Exception as e:
        log(f"다운로드 실패: {url} | {e}")
    return None


def upload_media_with_retry(path, retry=3):
    for i in range(retry):
        try:
            uploaded = mastodon.media_post(path)
            media_id = uploaded.get("id")

            if not media_id:
                raise Exception(f"no media id: {uploaded}")

            # processing wait
            for _ in range(10):
                m = mastodon.media(media_id)
                if m.get("url"):
                    return media_id
                time.sleep(1)

            log(f"processing timeout: {media_id}")

        except Exception as e:
            log(f"업로드 실패 retry {i+1}: {e}")
            time.sleep(2)

    return None


def process_images(drawn_urls):
    media_ids = []

    for url in drawn_urls:
        log(f"이미지 처리: {url}")

        img = download_image(url)
        if not img:
            log("다운로드 실패 skip")
            continue

        filename = f"/tmp/storebot_{random.randint(1000,9999)}.png"

        try:
            with open(filename, "wb") as f:
                f.write(img)

            media_id = upload_media_with_retry(filename)

            if media_id:
                media_ids.append(media_id)
                log(f"업로드 성공: {media_id}")
            else:
                log("업로드 최종 실패")

        finally:
            if os.path.exists(filename):
                os.remove(filename)

    return media_ids

# ===================== MAIN =====================

def process_mention(status):
    try:
        print("process_mention 시작")

        content = clean_html(status["content"])
        acct = status["account"]["acct"]
        user_handle = acct if acct.startswith("@") else f"@{acct}"

        shop_sheet, user_sheet, random_sheet = get_sheets()
        user_rows = user_sheet.get_all_values()

        user_idx = next(
            (i + 2 for i, row in enumerate(user_rows[1:])
             if row[0].strip().lower() == user_handle.lower()),
            -1
        )

        # ===== 구매 =====
        match_buy = re.search(r"\[구매\/(.+?)(?:\/(\d+))?\]", content)
        if match_buy:
            if user_idx == -1:
                return

            item_name = match_buy.group(1).strip()
            req_qty = int(match_buy.group(2)) if match_buy.group(2) else 1

            shop_rows = shop_sheet.get_all_values()

            prod_idx, prod_data = next(
                ((i + 2, row) for i, row in enumerate(shop_rows[1:])
                 if row[0].strip() == item_name),
                (-1, None)
            )

            if not prod_data:
                return

            total_price = safe_int(prod_data[2]) * req_qty
            current_money = safe_int(user_rows[user_idx - 1][3])

            if current_money < total_price:
                return

            inv = parse_inventory(user_rows[user_idx - 1][2])
            description = prod_data[1]

            drawn_urls = []
            media_ids = []

            # 랜덤 여부
            is_random = len(prod_data) > 7 and prod_data[7].strip() == "랜덤"

            if is_random:
                pool = [
                    {"name": r[1], "url": r[2]}
                    for r in random_sheet.get_all_values()[1:]
                    if r[0].strip() == item_name
                ]

                drawn_items = random.choices(pool, k=req_qty)
                for it in drawn_items:
                    if it["url"].startswith("http"):
                        drawn_urls.append(it["url"])
            else:
                inv[item_name] = inv.get(item_name, 0) + req_qty

            # 인벤 업데이트
            user_sheet.update_cell(user_idx, 3, rebuild_inventory(inv))
            user_sheet.update_cell(user_idx, 4, current_money - total_price)

            # ===== 이미지 안정 처리 =====
            media_ids = process_images(drawn_urls)

            # ===== 최종 툿 =====
            mastodon.status_post(
                status=f"@{acct}\n구매 완료",
                in_reply_to_id=status["id"],
                media_ids=media_ids if media_ids else None
            )

    except Exception as e:
        log(f"ERROR: {e}")

# ===================== LOOP =====================

if __name__ == "__main__":
    print("봇 시작")

    while True:
        try:
            since_id = load_since_id()

            notifications = mastodon.notifications(
                since_id=since_id,
                limit=20
            )

            if notifications:
                notifications.reverse()

                for n in notifications:
                    if n["type"] == "mention":
                        process_mention(n["status"])

                    save_since_id(n["id"])

            time.sleep(5)

        except Exception as e:
            log(f"LOOP ERROR: {e}")
            time.sleep(10)
