import re
import time
import os
import random
import requests
from dotenv import load_dotenv # 🌟 추가: .env 파일 읽기용
from mastodon import Mastodon, StreamListener # 🌟 추가: StreamListener
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from bs4 import BeautifulSoup

# 🌟 금고(.env) 열기
load_dotenv()

# ================= [ ⚙️ 필수 설정 구역 ] =================
MASTODON_SERVER = "https://by-of-garden.xyz"
# 🌟 직접 적는 대신, .env 파일에서 가져오기
ACCESS_TOKEN = os.getenv("MASTODON_ACCESS_TOKEN") 
JSON_FILE = "store-bot.json" # 🌟 아까 만든 구글 인증서 파일 이름으로 변경
SHEET_URL = os.getenv("GOOGLE_SHEET_URL")
# SINCE_ID_FILE 은 실시간 통신이므로 더 이상 필요하지 않아 삭제했습니다.
INITIAL_MONEY = 0 
# =======================================================

mastodon = Mastodon(access_token=ACCESS_TOKEN, api_base_url=MASTODON_SERVER)

def get_sheets():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(JSON_FILE, scope)
    client = gspread.authorize(creds)
    doc = client.open_by_url(SHEET_URL)
    return doc.worksheet("상점"), doc.worksheet("명단"), doc.worksheet("랜덤풀")

def clean_html(html_content):
    return BeautifulSoup(html_content, "html.parser").get_text()

def safe_int(value):
    if not value or str(value).strip() == "": return 0
    return int(str(value).replace(",", "").strip())

def parse_inventory(inv_string):
    if not inv_string or inv_string.strip() == "": return {}
    items = [i.strip() for i in inv_string.split("｜") if i.strip()]
    inv_dict = {}
    for item in items:
        match = re.match(r"^(.+?)\[(\d+)\]$", item)
        if match: inv_dict[match.group(1).strip()] = int(match.group(2))
        else: inv_dict[item] = 1
    return inv_dict

def rebuild_inventory(inv_dict):
    parts = [f"{name}[{count}]" if count > 1 else name for name, count in inv_dict.items() if count > 0]
    return " ｜ ".join(parts)

def process_mention(status):
    print("process_mention 시작")
    content = clean_html(status['content'])
    acct = status['account']['acct']
    user_handle = acct if acct.startswith('@') else f"@{acct}"
    
    try:
        shop_sheet, user_sheet, random_sheet = get_sheets() 
        user_rows = user_sheet.get_all_values()
        user_idx = next((i + 2 for i, row in enumerate(user_rows[1:]) if row[0].strip() == user_handle), -1)

        # 0. 신규 유저 등록
        if "[신입생 등록]" in content:
            if user_idx != -1:
                mastodon.status_post(status=f"@{acct} 이미 등록된 유저입니다.", in_reply_to_id=status['id'])
                return
            empty_row_idx = next((i + 2 for i, row in enumerate(user_rows[1:]) if not row[0].strip()), -1)
            if empty_row_idx == -1: return
            user_sheet.update_cell(empty_row_idx, 1, user_handle)
            user_sheet.update_cell(empty_row_idx, 2, acct)
            user_sheet.update_cell(empty_row_idx, 4, INITIAL_MONEY)
            mastodon.status_post(status=f"@{acct} 상점 이용이 가능합니다.", in_reply_to_id=status['id'])
            return

        # 2. 양도 기능
        match_trade = re.search(r"\[양도/(.+?)/(\d+)/(@.+?)\]", content)
        if match_trade:
            if user_idx == -1: return
            item_name = match_trade.group(1).strip()
            trade_count = int(match_trade.group(2))
            target_handle = match_trade.group(3).strip()
            
            # 내 인벤토리 확인
            inv_dict = parse_inventory(user_rows[user_idx-1][2])
            if inv_dict.get(item_name, 0) < trade_count:
                mastodon.status_post(status=f"@{acct} 해당 상품을 {trade_count}개만큼 가지고 있지 않습니다.", in_reply_to_id=status['id'])
                return
            
            # 타겟 유저 찾기 (아이디로 찾음)
            target_idx = next((i + 2 for i, row in enumerate(user_rows[1:]) if row[0].strip() == target_handle), -1)
            if target_idx == -1:
                mastodon.status_post(status=f"@{acct} {target_handle}님은 명단에 없는 유저입니다.", in_reply_to_id=status['id'])
                return
            
            # 🌟 [추가/수정] 타겟의 '이름(B열)' 가져오기 (user_rows의 인덱스는 0부터 시작하므로 target_idx-2)
            target_name = user_rows[target_idx-1][1] 
            
            # 인벤토리 데이터 갱신
            inv_dict[item_name] -= trade_count
            user_sheet.update_cell(user_idx, 3, rebuild_inventory(inv_dict))
            
            target_inv_dict = parse_inventory(user_rows[target_idx-1][2])
            target_inv_dict[item_name] = target_inv_dict.get(item_name, 0) + trade_count
            user_sheet.update_cell(target_idx, 3, rebuild_inventory(target_inv_dict))
            
            # 🌟 툿 출력도 '이름'으로 변경!
            mastodon.status_post(status=f"@{acct} {target_handle}\n\n{target_name}에게 [{item_name} {trade_count}개] 양도 완료", in_reply_to_id=status['id'])
            return
        # 2-2. 갈레온(재화) 양도 기능
        # 커맨드 형식: [갈레온 양도/금액/@아이디]
        match_money_trade = re.search(r"\[갈레온\s*양도/(\d+)/(@.+?)\]", content)
        if match_money_trade:
            if user_idx == -1: return
            
            transfer_amount = int(match_money_trade.group(1))
            target_handle = match_trade_target = match_money_trade.group(2).strip()
            
            # ① 0 이하의 금액 양도 방지
            if transfer_amount <= 0:
                mastodon.status_post(status=f"@{acct} 양도할 금액은 1 갈레온 이상이어야 합니다.", in_reply_to_id=status['id'])
                return
                
            current_money = safe_int(user_rows[user_idx-1][3])
            
            # ② 내 잔고 확인
            if current_money < transfer_amount:
                mastodon.status_post(status=f"@{acct} 갈레온이 부족합니다. (현재 보유: [{current_money:,}] 갈레온)", in_reply_to_id=status['id'])
                return
                
            # ③ 받을 사람(타겟)이 명단에 있는지 확인
            target_idx = next((i + 2 for i, row in enumerate(user_rows[1:]) if row[0].strip() == target_handle), -1)
            if target_idx == -1:
                return
                
            # ④ 받는 사람의 이름(B열)과 현재 돈 가져오기
            target_name = user_rows[target_idx-1][1]
            target_current_money = safe_int(user_rows[target_idx-1][3])
            
            # ⑤ 구글 시트에 업데이트 (내 돈 -, 상대 돈 +)
            user_sheet.update_cell(user_idx, 4, current_money - transfer_amount)
            user_sheet.update_cell(target_idx, 4, target_current_money + transfer_amount)
            
            # ⑥ 완료 영수증 툿 발송 (이름으로 출력!)
            mastodon.status_post(
                status=f"@{acct}\n{target_name} 에게 [{transfer_amount:,}] 갈레온을 안전하게 보냈습니다.", 
                in_reply_to_id=status['id']
            )
            return

        # 3. 구매 기능
        match_buy = re.search(r"\[구매\/(.+?)(?:\/(\d+))?\]", content)
        if match_buy:
            if user_idx == -1: return
            item_name, req_qty = match_buy.group(1).strip(), int(match_buy.group(2)) if match_buy.group(2) else 1
            
            shop_rows = shop_sheet.get_all_values()
            prod_idx, prod_data = next(((i+2, row) for i, row in enumerate(shop_rows[1:]) if row[0].strip() == item_name), (-1, None))
            
            if not prod_data or prod_data[6].strip().upper() == "FALSE": return

            total_price = safe_int(prod_data[2]) * req_qty
            total_give_qty = (safe_int(prod_data[3]) if prod_data[3].strip() else 1) * req_qty
            current_money = safe_int(user_rows[user_idx-1][3])
            
            if current_money < total_price: return
            
            is_random = len(prod_data) > 7 and prod_data[7].strip() == "랜덤"
            inv_dict = parse_inventory(user_rows[user_idx-1][2])
            description = prod_data[1]
            result_display, drawn_urls, media_ids = "", [], []

            if is_random:
                pool = [{"name": r[1].strip(), "url": r[2].strip() if len(r)>2 else ""} for r in random_sheet.get_all_values()[1:] if r[0].strip() == item_name]
                if not pool: return
                drawn_items = [random.choice(pool) for _ in range(total_give_qty)]
                drawn_names = [i["name"] for i in drawn_items]
                for name in drawn_names: inv_dict[name] = inv_dict.get(name, 0) + 1
                result_display = ", ".join(drawn_names)
                description = description.replace("{결과}", result_display)
                drawn_urls = [i["url"] for i in drawn_items if i["url"].startswith("http")]
            else:
                inv_dict[item_name] = inv_dict.get(item_name, 0) + total_give_qty
                result_display = item_name

            user_sheet.update_cell(user_idx, 3, rebuild_inventory(inv_dict))
            user_sheet.update_cell(user_idx, 4, current_money - total_price)
            shop_sheet.update_cell(prod_idx, 6, safe_int(prod_data[5]) + req_qty)

            for url in drawn_urls:
                try:
                    res = requests.get(url)
                    if res.status_code == 200:
                        with open("temp.png", "wb") as f: f.write(res.content)
                        media_ids.append(mastodon.media_post("temp.png")['id'])
                except: pass

            mastodon.status_post(
                status=f"@{acct}\n⟡ '{item_name}' {req_qty}개를 구매했습니다. ⟡\n\n[ {description} ]\n[ {result_display} ] 소지품에 들어갔습니다. \n\n[ 금액: {total_price:,} G ｜ 잔액: {current_money - total_price:,} G ]",
                in_reply_to_id=status['id'], media_ids=media_ids if media_ids else None
            )

    except Exception as e:
        print(f"오류: {e}")

# ================= [ 📡 실시간 스트리밍 리스너 클래스 ] =================
class BotListener(StreamListener):
    def on_notification(self, notification):
        print("===== 알림 도착 =====")
        print(notification)

        if notification['type'] == 'mention':
            print("===== 멘션 감지 =====")
            process_mention(notification['status'])

# ================= [ 🚀 봇 실행 구역 ] =================
if __name__ == "__main__":
    print("✨ 상점 봇(실시간 스트리밍 모드) 활성화 완료!")
    
    # 귀를 쫑긋 세우는 리스너 객체 생성
    listener = BotListener()
    
    # 무한 대기하며 멘션이 오면 즉각 반응 (서버 부하 제로!)
    while True:
        try:
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 📡 스트리밍 연결")
            mastodon.stream_user(listener)
    
            print("⚠️ 스트리밍 종료됨")
    
        except Exception as e:
            print(f"🚨 스트리밍 오류: {e}")
    
        print("🔄 10초 후 재연결")
        time.sleep(10)
