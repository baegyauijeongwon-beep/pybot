import time
import random
import os
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv


# -----------------------------
# ENV LOAD
# -----------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

ACCESS_TOKEN = os.getenv("ROLL_BOT")


# -----------------------------
# BOT CLASS
# -----------------------------
class MastodonBot:
    def __init__(self, base_url, access_token, poll_interval=5):
        self.base_url = "https://by-of-garden.xyz"
        self.access_token = access_token
        self.poll_interval = poll_interval

        self.headers = {
            "Authorization": f"Bearer {self.access_token}"
        }

        self.last_seen_id = None

    # -------------------------
    # UTIL
    # -------------------------
    def clean_text(self, html):
        return BeautifulSoup(html, "html.parser").get_text().lower().strip()

    # -------------------------
    # MAIN LOOP
    # -------------------------
    def run(self):
        print("Bot started...")
        while True:
            try:
                self.check_notifications()
            except Exception as e:
                print("Error:", e)

            time.sleep(self.poll_interval)

    # -------------------------
    # FETCH NOTIFICATIONS
    # -------------------------
    def check_notifications(self):
        url = f"{self.base_url}/api/v1/notifications"
        res = requests.get(url, headers=self.headers, timeout=10)
        notifications = res.json()

        for n in reversed(notifications):
            if self.last_seen_id and int(n["id"]) <= int(self.last_seen_id):
                continue

            if n["type"] == "mention":
                self.on_notification(n)

            self.last_seen_id = n["id"]

    import re
    
    def on_notification(self, notification):
        status = notification['status']
        user = status['account']['acct']
        status_id = status['id']
    
        content = self.clean_text(status['content'])
    
        reply_text = f"@{user} "
    
        # -------------------------
        # ONLY [COMMAND] MATCH
        # -------------------------
        match = re.search(r"\[(.*?)\]", content)
    
        if not match:
            return  # [] 없으면 완전 무시
    
        command = match.group(1).strip().lower()
    
        if command == "1d100":
            reply_text += f"주사위 결과: {random.randint(1, 100)}"
    
        elif command == "1d10":
            reply_text += f"주사위 결과: {random.randint(1, 10)}"
    
        elif command == "가위바위보":
            reply_text += f"결과: {random.choice(['가위', '바위', '보'])}"
    
        elif command == "yn":
            reply_text += f"결과: {random.choice(['YES', 'NO'])}"
    
        else:
            return  # [] 안이어도 모르는 커맨드면 무시
    
        self.reply(status_id, reply_text)
        # -------------------------
        # REPLY
        # -------------------------
        def reply(self, status_id, text):
            url = f"{self.base_url}/api/v1/statuses"
    
            data = {
                "status": text,
                "in_reply_to_id": status_id,
                "visibility": "public"
            }
    
            res = requests.post(url, headers=self.headers, data=data)
    
            if res.status_code != 200:
                print("Reply failed:", res.text)
    

# -----------------------------
# RUN
# -----------------------------
if __name__ == "__main__":
    bot = MastodonBot(
        base_url="https://your.instance.url",
        access_token=ACCESS_TOKEN,
        poll_interval=5
    )

    bot.run()
