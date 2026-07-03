import re
import time
import os
import random
from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from mastodon import Mastodon
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from bs4 import BeautifulSoup

# ================= [ 🌟 .env 로드 ] =================

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

load_dotenv(os.path.join(BASE_DIR, ".env"))

# ================= [ ⚙️ 필수 설정 ] =================

MASTODON_SERVER = "https://by-of-garden.xyz"

ACCESS_TOKEN = os.getenv("SYSTEM_BOTT")
WISH_SHEET_URL = os.getenv("WISH_SHEET_URL")

JSON_FILE = os.path.join(BASE_DIR, "store-bot.json")
SHEET_URL = os.getenv("GOOGLE_SHEET_URL")

SINCE_ID_FILE = "attendance_last_notification.txt"

# ==================================================

print("TOKEN 존재 여부:", ACCESS_TOKEN is not None)
print("TOKEN 길이:", len(ACCESS_TOKEN) if ACCESS_TOKEN else 0)

mastodon = Mastodon(
    access_token=ACCESS_TOKEN,
    api_base_url=MASTODON_SERVER
)

acct = mastodon.account_verify_credentials()
print("로그인 계정:", acct["acct"])

notifications = mastodon.notifications(limit=5)

for n in notifications:
    print("알림 종류:", n["type"])


# ================= [ 공용 함수 ] =================

def get_sheet():
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive"
    ]

    creds = ServiceAccountCredentials.from_json_keyfile_name(
        JSON_FILE,
        scope
    )

    client = gspread.authorize(creds)
    doc = client.open_by_url(SHEET_URL)

    return doc.worksheet("명단")


def clean_html(html_content):
    return BeautifulSoup(
        html_content,
        "html.parser"
    ).get_text()


def safe_int(value):
    if not value or str(value).strip() == "":
        return 0

    return int(
        str(value)
        .replace(",", "")
        .strip()
    )


def load_since_id():
    if os.path.exists(SINCE_ID_FILE):
        with open(SINCE_ID_FILE, "r") as f:
            return int(f.read().strip())

    return None


def save_since_id(notification_id):
    with open(SINCE_ID_FILE, "w") as f:
        f.write(str(notification_id))


#상시이벤트 추가

def get_wish_sheet(sheet_name):
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive"
    ]

    creds = ServiceAccountCredentials.from_json_keyfile_name(
        JSON_FILE,
        scope
    )

    client = gspread.authorize(creds)
    doc = client.open_by_url(WISH_SHEET_URL)

    return doc.worksheet(sheet_name)

def handle_wish_piece(status, acct):
    today = datetime.now(
        ZoneInfo("Asia/Seoul")
    ).strftime("%Y-%m-%d")

    user_handle = (
        acct
        if acct.startswith("@")
        else f"@{acct}"
    )

    piece_sheet = get_wish_sheet("소원 조각")
    log_sheet = get_wish_sheet("조각 모음 기록")

    log_rows = log_sheet.get_all_values()

    user_log_idx = next(
        (
            i + 2
            for i, row in enumerate(log_rows[1:])
            if len(row) > 0
            and row[0].strip().lower() == user_handle.lower()
        ),
        -1
    )

    if user_log_idx != -1:
        last_date = ""

        if len(log_rows[user_log_idx - 1]) > 1:
            last_date = log_rows[user_log_idx - 1][1].strip()

        if last_date == today:
            mastodon.status_post(
                status=(
                    f"@{acct}\n"
                    f"오늘 조각은 이미 찾았다."
                ),
                in_reply_to_id=status["id"]
            )

            return

    piece_rows = piece_sheet.get_all_values()

    candidates = []

    for i, row in enumerate(piece_rows[1:], start=2):
        code = row[0].strip() if len(row) > 0 else ""
        piece = row[2].strip() if len(row) > 2 else ""
        checked = row[3].strip().upper() if len(row) > 3 else "FALSE"

        if code and piece and checked != "TRUE":
            candidates.append(
                {
                    "row": i,
                    "code": code,
                    "piece": piece
                }
            )

    if not candidates:
        mastodon.status_post(
            status=(
                f"@{acct}\n"
                f"남아있는 조각이 없습니다."
            ),
            in_reply_to_id=status["id"]
        )

        return

    selected = random.choice(candidates)

    piece_sheet.update_cell(
        selected["row"],
        4,
        "TRUE"
    )

    if user_log_idx == -1:
        log_sheet.append_row(
            [
                user_handle,
                today
            ]
        )
    else:
        log_sheet.update_cell(
            user_log_idx,
            2,
            today
        )

    mastodon.status_post(
        status=(
            f"@{acct}\n"
            f"소원 조각을 발견했다.🌟\n\n"
            f"코드: {selected['code']}\n"
            f"조각 내용: {selected['piece']}"
        ),
        in_reply_to_id=status["id"]
    )


# ================= [ 커맨드 처리 ] =================

def process_mention(status):

    print("process_mention 시작")

    content = clean_html(status["content"])

    acct = status["account"]["acct"]

    user_handle = (
        acct
        if acct.startswith("@")
        else f"@{acct}"
    )

    try:

        user_sheet = get_sheet()

        user_rows = user_sheet.get_all_values()

        user_idx = next(
            (
                i + 2
                for i, row in enumerate(user_rows[1:])
                if row[0].strip().lower()
                == user_handle.lower()
            ),
            -1
        )

        if user_idx == -1:
            return
          
        # =====================
        # [소원 조각]
        # =====================

        if "[소원 조각]" in content:

            handle_wish_piece(
                status,
                acct
            )

            return
        # =====================
        # [저금 50]
        # =====================

        if "[저금 50]" in content:

            current_money = safe_int(
                user_rows[user_idx - 1][3]
            )

            user_sheet.update_cell(
                user_idx,
                4,
                current_money + 1
            )

            mastodon.status_post(
                status=(
                    f"@{acct}\n"
                    f"50툿 확인 되었습니다!\n"
                    f"+1 갈레온"
                ),
                in_reply_to_id=status["id"]
            )

            return

        # =====================
        # [저금 100]
        # =====================

        if "[저금 100]" in content:

            current_money = safe_int(
                user_rows[user_idx - 1][3]
            )

            user_sheet.update_cell(
                user_idx,
                4,
                current_money + 2
            )

            mastodon.status_post(
                status=(
                    f"@{acct}\n"
                    f"100툿 확인 되었습니다!\n"
                    f"+2 갈레온"
                ),
                in_reply_to_id=status["id"]
            )

            return

        # =====================
        # [출석]
        # =====================

        if "[출석]" in content:

            now = datetime.now(
                ZoneInfo("Asia/Seoul")
            )

            current_time = now.time()

            start_time = dt_time(
                hour=9,
                minute=0,
                second=0
            )

            end_time = dt_time(
                hour=14,
                minute=0,
                second=0
            )

            if not (
                start_time
                <= current_time
                <= end_time
            ):

                mastodon.status_post(
                    status=(
                        f"@{acct}\n"
                        f"지각생에게 갈레온은 없다."
                    ),
                    in_reply_to_id=status["id"]
                )

                return

            current_money = safe_int(
                user_rows[user_idx - 1][3]
            )

            user_sheet.update_cell(
                user_idx,
                4,
                current_money + 1
            )

            mastodon.status_post(
                status=(
                    f"@{acct}\n"
                    f"출석 완료.\n"
                    f"+1 갈레온 지급."
                ),
                in_reply_to_id=status["id"]
            )

            return

    except Exception as e:
        print(f"오류: {e}")


# ================= [ 🚀 실행 ] =================

if __name__ == "__main__":

    print("✨ 출석/저금 봇 활성화 완료!")

    while True:

        try:

            since_id = load_since_id()

            notifications = mastodon.notifications(
                since_id=since_id,
                limit=20
            )

            if notifications:

                notifications.reverse()

                for notification in notifications:

                    print(
                        f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] "
                        f"알림: {notification['type']}"
                    )

                    if notification["type"] == "mention":

                        print(
                            f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] "
                            f"멘션 수신: "
                            f"{notification['account']['acct']}"
                        )

                        process_mention(
                            notification["status"]
                        )

                    save_since_id(
                        notification["id"]
                    )

            # 상점봇(5초)과 타이밍 살짝 분리
            time.sleep(5.3)

        except Exception as e:

            print("🚨 오류:", e)

            time.sleep(10)
