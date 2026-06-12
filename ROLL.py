import time
import random
import os
import re
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv


# -----------------------------
# ENV LOAD
# -----------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv("/home/baegyauijeongwon/mastodon_bot/.env")

ACCESS_TOKEN = os.getenv("ROLL_BOT")


# -----------------------------
# BOT CLASS
# -----------------------------
class MastodonBot:
    def __init__(self, access_token, poll_interval=5):
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
        self.load_last_id()
        
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
    
        try:
            notifications = res.json()
        except:
            print("API error:", res.text)
            return
    
        latest_id = self.last_seen_id
    
        for n in reversed(notifications):
            if self.last_seen_id and int(n["id"]) <= int(self.last_seen_id):
                continue
    
            if n["type"] == "mention":
                self.on_notification(n)
    
            latest_id = n["id"]
    
        self.last_seen_id = latest_id
        self.save_last_id()

    # -------------------------
    # HANDLER
    # -------------------------
    def on_notification(self, notification):
        status = notification["status"]
        user = status["account"]["acct"]
        status_id = status["id"]

        content = self.clean_text(status["content"])
        reply_text = f"@{user} "

        match = re.search(r"\[(.*?)\]", content)

        if not match:
            return

        command = match.group(1).strip().lower()

        if command == "1d100":
            reply_text += f"1D100: {random.randint(1, 100)}"
        elif command == "1d10":
            reply_text += f"1D10: {random.randint(1, 10)}"
        elif command == "가위바위보":
            reply_text += f"{random.choice(['가위','바위','보'])}"
        elif command == "yn":
            reply_text += f"{random.choice(['YES','NO'])}"
        else:
            return

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

    def save_last_id(self):
        with open("last_notification.txt", "w") as f:
            f.write(str(self.last_seen_id))
    
    
    def load_last_id(self):
        try:
            with open("last_notification.txt", "r") as f:
                value = f.read().strip()
                self.last_seen_id = value if value else None
        except:
            self.last_seen_id = None



# -----------------------------
# RUN
# -----------------------------
if __name__ == "__main__":
    bot = MastodonBot(
    access_token=ACCESS_TOKEN,
    poll_interval=5
)

    bot.run()
