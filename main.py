import os
import json
import re
import asyncio
import requests
import feedparser
import google.generativeai as genai
import edge_tts
from moviepy.editor import VideoFileClip, AudioFileClip, TextClip, CompositeVideoClip

# --- CONFIGURAZIONI ---
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
PEXELS_KEY = os.environ.get("PEXELS_API_KEY")

genai.configure(api_key=GEMINI_KEY)
model = genai.GenerativeModel('gemini-1.5-flash')

RSS_URL = "INCOLLA_QUI_IL_TUO_LINK_FEED_RSS"
LOG_FILE = "processed_links.txt"


# --- FUNZIONI DI UTILITA' ---
def get_processed_links():
    if not os.path.exists(LOG_FILE): return set()
    with open(LOG_FILE, "r") as f: return set(f.read().splitlines())


def save_link(link):
    with open(LOG_FILE, "a") as f: f.write(link + "\n")


def clean_json(text):
    # Pulisce l'output di Gemini se aggiunge i backtick del markdown
    text = re.sub(r'^```json\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'```$', '', text, flags=re.MULTILINE)
    return text.strip()


async def create_audio(text, filename="audio.mp3"):
    voice = "it-IT-GiuseppeNeural"  # Voce maschile italiana (molto realistica)
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(filename)


def download_pexels_video(query, filename="background.mp4"):
    headers = {"Authorization": PEXELS_KEY}
    # Cerchiamo video verticali (portrait) perfetti per TikTok/Shorts
    url = f"https://api.pexels.com/videos/search?query={query}&orientation=portrait&per_page=1"
    response = requests.get(url, headers=headers).json()

    if "videos" in response and len(response["videos"]) > 0:
        video_files = response["videos"][0]["video_files"]
        # Prendiamo la versione HD
        hd_file = next((f for f in video_files if f['quality'] == 'hd'), video_files[0])
        video_url = hd_file['link']

        with open(filename, 'wb') as f:
            f.write(requests.get(video_url).content)
        return True
    return False


def make_video(hook_text, audio_path, bg_path, output_path="final_video.mp4"):
    # Carichiamo video e audio
    video = VideoFileClip(bg_path)
    audio = AudioFileClip(audio_path)

    # Tagliamo o allunghiamo il video per farlo durare quanto l'audio
    if video.duration < audio.duration:
        # Se il video è corto, lo facciamo ripetere (loop)
        video = video.loop(duration=audio.duration)
    else:
        video = video.subclip(0, audio.duration)

    video = video.set_audio(audio)

    # Creiamo una scritta con l'Hook da mettere al centro (richiede ImageMagick su GitHub)
    txt_clip = TextClip(hook_text, fontsize=50, color='white', bg_color='black',
                        font='Arial-Bold', method='caption', size=(video.w * 0.8, None))
    txt_clip = txt_clip.set_position('center').set_duration(video.duration)

    # Sovrapponiamo il testo al video
    final_clip = CompositeVideoClip([video, txt_clip])

    # Esportiamo il video!
    final_clip.write_videofile(output_path, fps=24, codec="libx264", audio_codec="aac")


# --- FLUSSO PRINCIPALE ---
def run():
    feed = feedparser.parse(RSS_URL)
    processed = get_processed_links()

    for entry in feed.entries:
        if entry.link not in processed:
            print(f"Lavoro sulla notizia: {entry.title}")

            # 1. Chiediamo lo Script a Gemini
            prompt = f"""
            Analizza la notizia: {entry.title} - {entry.summary}.
            Crea uno script di 30 secondi.
            Restituisci SOLO un JSON con:
            "hook": Frase d'apertura breve e shock (max 10 parole).
            "voiceover": Il testo completo da leggere ad alta voce (incluso l'hook all'inizio).
            "search_term": 1 o 2 parole chiave in INGLESE per cercare un video di sfondo (es. "technology", "space", "business").
            """
            response = model.generate_content(prompt)
            script_data = json.loads(clean_json(response.text))

            print(f"Script generato. Cerco video per: {script_data['search_term']}")

            # 2. Scarichiamo il video da Pexels
            if download_pexels_video(script_data['search_term']):
                print("Video di sfondo scaricato.")

                # 3. Creiamo l'audio neurale
                asyncio.run(create_audio(script_data["voiceover"]))
                print("Audio generato.")

                # 4. Montiamo il video
                make_video(script_data["hook"], "audio.mp3", "background.mp4")
                print("VIDEO FINITO!")
            else:
                print("Nessun video trovato su Pexels, salto la notizia.")

            # Salviamo il link per non ripeterlo
            save_link(entry.link)
            break  # Facciamo solo un video per ogni esecuzione di GitHub Actions


if __name__ == "__main__":
    run()