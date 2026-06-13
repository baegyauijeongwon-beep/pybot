import re
import time
import os
import random
import requests
from dotenv import load_dotenv
from mastodon import Mastodon
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from bs4 import BeautifulSoup

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE_DIR, ".env"))

MASTODON_SERVER = "https://by-of-garden.xyz"
ACCESS_TOKEN = os.getenv("MASTODON_ACCESS_TOKEN")
JSON_FILE = os.path.join(BASE_DIR, "store-bot.json")
SHEET_URL = os.getenv("GOOGLE_SHEET_URL")

INITIAL_MONEY = 0
SINCE_ID_FILE = "store_last_notification.txt"

print("TOKEN 존재 여부:", ACCESS_TOKEN is not None)
print("TOKEN 길이:", len(ACCESS_TOKEN) if ACCESS_TOKEN else 0)

mastodon = Mastodon(access_token=ACCESS_TOKEN, api_base_url=MASTODON_SERVER)
acct = mastodon.account_verify_credentials()
print("로그인 계정:", acct["acct"])


def get_sheets():
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = ServiceAccountCredentials.from_json_keyfile_name(JSON_FILE, scope)
    client = gspread.authorize(creds)
    doc = client.open_by_url(SHEET_URL)
    return doc.worksheet("상점"), doc.worksheet("명단"), doc.worksheet("랜덤풀")


def clean_html(html_content):
    return BeautifulSoup(html_content, "html.parser").get_text()


def safe_int(value):
    if not value or str(value).strip() == "":
        return 0
    return int(str(value).replace(",", "").strip())


def parse_inventory(inv_string):
    if not inv_string or inv_string.strip() == "":
        return {}
    items = [i.strip() for i in inv_string.split("｜") if i.strip()]
    inv_dict = {}
    for item in items:
        match = re.match(r"^(.+?)\[(\d+)\]$", item)
        if match:
            inv_dict[match.group(1).strip()] = int(match.group(2))
        else:
            inv_dict[item] = 1
    return inv_dict


def rebuild_inventory(inv_dict):
    return " ｜ ".join(
        [f"{k}[{v}]" if v > 1 else k for k, v in inv_dict.items() if v > 0]
    )


def load_since_id():
    if os.path.exists(SINCE_ID_FILE):
        with open(SINCE_ID_FILE, "r") as f:
            return int(f.read().strip())
    return None


def save_since_id(notification_id):
    with open(SINCE_ID_FILE, "w") as f:
        f.write(str(notification_id))


def process_mention(status):
    print("process_mention 시작")

    content = clean_html(status["content"])
    acct = status["account"]["acct"]
    user_handle = acct if acct.startswith("@") else f"@{acct}"

    try:
        shop_sheet, user_sheet, random_sheet = get_sheets()
        user_rows = user_sheet.get_all_values()

        user_idx = next(
            (i + 2 for i, row in enumerate(user_rows[1:])
             if row[0].strip().lower() == user_handle.lower()),
            -1
        )

        # =====================
        # 0. 신규 유저
        # =====================
        if "[신입생 등록]" in content:
            if user_idx != -1:
                mastodon.status_post(
                    status=f"@{acct} 이미 명단에 있습니다.",
                    in_reply_to_id=status["id"]
                )
                return

            empty_row_idx = next(
                (i + 2 for i, row in enumerate(user_rows[1:]) if not row[0].strip()),
                -1
            )
            if empty_row_idx == -1:
                return

            user_sheet.update_cell(empty_row_idx, 1, user_handle)
            user_sheet.update_cell(empty_row_idx, 2, status["account"]["display_name"])
            user_sheet.update_cell(empty_row_idx, 4, INITIAL_MONEY)

            mastodon.status_post(
                status=f"@{acct} 상점 이용 가능",
                in_reply_to_id=status["id"]
            )
            return

        # =====================
        # 2. 구매
        # =====================
        match_buy = re.search(r"\[구매\/(.+?)(?:\/(\d+))?\]", content)
        if match_buy and user_idx != -1:

            item_name = match_buy.group(1).strip()
            req_qty = int(match_buy.group(2)) if match_buy.group(2) else 1

            shop_rows = shop_sheet.get_all_values()
            prod_idx, prod_data = next(
                ((i + 2, row) for i, row in enumerate(shop_rows[1:])
                 if row[0].strip() == item_name),
                (-1, None)
            )

            if not prod_data or prod_data[6].strip().upper() == "FALSE":
                return

            total_price = safe_int(prod_data[2]) * req_qty
            total_give_qty = (safe_int(prod_data[3]) or 1) * req_qty
            current_money = safe_int(user_rows[user_idx - 1][3])

            if current_money < total_price:
                return

            inv_dict = parse_inventory(user_rows[user_idx - 1][2])
            description = prod_data[1]

            result_display = ""
            drawn_urls = []
            media_ids = []

            is_random = len(prod_data) > 7 and prod_data[7].strip() == "랜덤"

            if is_random:
                pool = [
                    {"name": r[1], "url": r[2] if len(r) > 2 else ""}
                    for r in random_sheet.get_all_values()[1:]
                    if r[0].strip() == item_name
                ]

                drawn = [random.choice(pool) for _ in range(total_give_qty)]
                names = [d["name"] for d in drawn]

                for n in names:
                    inv_dict[n] = inv_dict.get(n, 0) + 1

                result_display = ", ".join(names)
                description = description.replace("{결과}", result_display)
                drawn_urls = [d["url"] for d in drawn if d["url"].startswith("http")]

            else:
                inv_dict[item_name] = inv_dict.get(item_name, 0) + total_give_qty
                result_display = item_name

            user_sheet.update_cell(user_idx, 3, rebuild_inventory(inv_dict))
            user_sheet.update_cell(user_idx, 4, current_money - total_price)
            shop_sheet.update_cell(prod_idx, 6, safe_int(prod_data[5]) + req_qty)

            # =====================
            # 🔥 이미지 안정 업로드
            # =====================
            for url in drawn_urls:
                try:
                    print("IMG:", url)

                    res = requests.get(url, timeout=10)
                    if res.status_code != 200:
                        continue

                    import uuid
                    filename = f"/tmp/{uuid.uuid4().hex}.png"

                    with open(filename, "wb") as f:
                        f.write(res.content)

                    uploaded = mastodon.media_post(filename)
                    media_id = uploaded["id"]

                    # processing wait
                    for _ in range(15):
                        m = mastodon.media(media_id)
                        if m.get("url"):
                            break
                        time.sleep(1)

                    media_ids.append(media_id)

                    os.remove(filename)

                except Exception as e:
                    print("IMG ERROR:", e)

            mastodon.status_post(
                status=f"@{acct}\n구매 완료\n\n[{description}]\n[{result_display}]",
                in_reply_to_id=status["id"],
                media_ids=media_ids if len(media_ids) > 0 else None
            )

    except Exception as e:
        print("ERROR:", e)


# =====================
# LOOP
# =====================
if __name__ == "__main__":

    print("BOT START")

    while True:
        try:
            since_id = load_since_id()

            notifications = mastodon.notifications(
                since_id=since_id,
                limit=20
            )

            notifications.reverse()

            for n in notifications:
                print("NOTI:", n["type"])

                if n["type"] == "mention":
                    process_mention(n["status"])

                save_since_id(n["id"])

            time.sleep(5)

        except Exception as e:
            print("LOOP ERROR:", e)
            time.sleep(10)
