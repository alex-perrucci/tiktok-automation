import os
import feedparser
import google.generativeai as genai

# 1. Configurazione API Gemini
genai.configure(api_key=os.environ["GEMINI_API_KEY"])
model = genai.GenerativeModel('gemini-3-flash-preview')

RSS_URL = "https://www.google.it/alerts/feeds/01389272250505533510/7146043719198930595"
LOG_FILE = "processed_links.txt"


def get_processed_links():
    if not os.path.exists(LOG_FILE): return set()
    with open(LOG_FILE, "r") as f:
        return set(f.read().splitlines())


def save_link(link):
    with open(LOG_FILE, "a") as f:
        f.write(link + "\n")


def run():
    feed = feedparser.parse(RSS_URL)
    processed = get_processed_links()

    for entry in feed.entries:
        if entry.link not in processed:
            print(f"--- Nuova notizia: {entry.title} ---")

            # 2. Chiediamo a Gemini di creare lo script per il video
            prompt = f"""
            Analizza questa notizia: {entry.title}. 
            Riassunto: {entry.summary}.
            Crea uno script per un video TikTok/Shorts di 30 secondi.
            Restituisci solo un JSON con queste chiavi:
            "hook": una frase d'apertura forte,
            "body": 3 punti chiave brevi,
            "call_to_action": invito a seguire il canale.
            """

            response = model.generate_content(prompt)
            print("Script Generato:")
            print(response.text)

            # Qui in futuro aggiungeremo la chiamata all'API video (es. Shotstack o HeyGen)

            save_link(entry.link)
            break  # Ne processiamo una alla volta per non sovraccaricare il test


if __name__ == "__main__":
    run()