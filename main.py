import os
import json
import re
import asyncio
import math
import requests
import feedparser
from google import genai  # <-- Il nuovo SDK ufficiale
import edge_tts
from moviepy.editor import VideoFileClip, AudioFileClip, TextClip, CompositeVideoClip, CompositeAudioClip, ImageClip
import PIL.Image
if not hasattr(PIL.Image, 'ANTIALIAS'):
    PIL.Image.ANTIALIAS = PIL.Image.LANCZOS
from moviepy.audio.fx.all import audio_loop, volumex

# --- CONFIGURAZIONI ---
# Il nuovo SDK pesca in automatico la variabile d'ambiente GEMINI_API_KEY
client = genai.Client()
PEXELS_KEY = os.environ.get("PEXELS_API_KEY")

RSS_URL = "https://www.google.it/alerts/feeds/01389272250505533510/7146043719198930595"  # <- Ricordati di rimettere il tuo link!
LOG_FILE = "processed_links.txt"


# --- FUNZIONI DI UTILITA' ---
def get_processed_links():
    if not os.path.exists(LOG_FILE): return set()
    with open(LOG_FILE, "r") as f: return set(f.read().splitlines())


def save_link(link):
    with open(LOG_FILE, "a") as f: f.write(link + "\n")


def clean_json(text):
    text = re.sub(r'^```json\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'```$', '', text, flags=re.MULTILINE)
    return text.strip()


async def create_audio(text, filename="audio.mp3"):
    voice = "it-IT-GiuseppeNeural"
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(filename)


def download_pexels_video(query, filename="background.mp4"):
    headers = {"Authorization": PEXELS_KEY}
    url = f"https://api.pexels.com/videos/search?query={query}&orientation=portrait&per_page=1"
    response = requests.get(url, headers=headers).json()

    if "videos" in response and len(response["videos"]) > 0:
        video_files = response["videos"][0]["video_files"]
        hd_file = next((f for f in video_files if f['quality'] == 'hd'), video_files[0])
        video_url = hd_file['link']

        with open(filename, 'wb') as f:
            f.write(requests.get(video_url).content)
        return True
    return False


def make_video(full_text, audio_path, bg_path, output_path="final_video.mp4"):
    # 1. Carica le clip
    video = VideoFileClip(bg_path)
    voice_audio = AudioFileClip(audio_path)

    # --- MIXAGGIO MUSICA DI SOTTOFONDO ---
    try:
        from moviepy.audio.fx.all import audio_loop, volumex
        print("Provo a caricare la musica di sottofondo...")
        bg_music = AudioFileClip("bg_music.mp3")

        # Faccio durare la musica quanto la voce
        bg_music = audio_loop(bg_music, duration=voice_audio.duration)
        # Abbasso il volume al 15%
        bg_music = volumex(bg_music, 0.15)

        final_audio = CompositeAudioClip([voice_audio, bg_music])
        print("Musica mixata con successo!")

    except Exception as e:
        print(f"ATTENZIONE: Impossibile usare la musica. Errore reale: {e}")
        final_audio = voice_audio

    # 2. Loop o Taglio del video di sfondo in base all'audio finale
    if video.duration < final_audio.duration:
        video = video.loop(duration=final_audio.duration)
    else:
        video = video.subclip(0, final_audio.duration)

    video = video.set_audio(final_audio)

    # 3. FORZATURA FORMATO TIKTOK (9:16)
    target_ratio = 9 / 16
    current_ratio = video.w / video.h

    if current_ratio > target_ratio:
        new_width = int(video.h * target_ratio)
        video = video.crop(x_center=video.w / 2, y_center=video.h / 2, width=new_width, height=video.h)
    else:
        new_height = int(video.w / target_ratio)
        video = video.crop(x_center=video.w / 2, y_center=video.h / 2, width=video.w, height=new_height)

    video = video.resize(newsize=(1080, 1920))

    # --- NOVITÀ: AVATAR AL CENTRO (Trucco del Marketer) ---
    try:
        print("Provo a inserire l'Avatar (podcaster.png)...")
        # Carichiamo l'immagine che hai scelto
        avatar = ImageClip("podcaster.png")

        # Rendiamo l'avatar bello grande (600 pixel di larghezza)
        avatar = avatar.resize(width=600)
        avatar = avatar.set_duration(video.duration)

        # Animazione Respiro: Centro orizzontale ('center'),
        # asse Y impostato a 750 (metà superiore) con un'oscillazione di 15 pixel
        avatar = avatar.set_position(lambda t: ('center', 750 + 15 * math.sin(t * 3)))

        avatar_layer = [avatar]
        print("Avatar animato aggiunto con successo!")
    except Exception as e:
        print(f"Nessun avatar trovato. Errore: {e}")
        avatar_layer = []

    # 4. SOTTOTITOLI DINAMICI STILE "HORMOZI" (Spostati in basso)
    words = full_text.split()
    chunk_size = 3
    chunks = [words[i:i + chunk_size] for i in range(0, len(words), chunk_size)]

    time_per_word = voice_audio.duration / len(words)
    subtitle_clips = []
    current_time = 0

    for chunk_words in chunks:
        chunk_text = " ".join(chunk_words)
        chunk_duration = len(chunk_words) * time_per_word

        txt_clip = TextClip(
            chunk_text,
            fontsize=85,
            color='yellow',
            font='Arial-Bold',
            stroke_color='black',
            stroke_width=3.5,
            method='caption',
            size=(900, None),
            align='center'
        )

        # SPOSTATI IN BASSO: Invece di 'center' assoluto, li mettiamo alla coordinata Y=1350
        # Così staranno esattamente sotto il podcaster senza coprirlo!
        txt_clip = txt_clip.set_position(('center', 1350)) \
            .set_start(current_time) \
            .set_duration(chunk_duration)

        subtitle_clips.append(txt_clip)
        current_time += chunk_duration

    # 5. Composizione: Ordine esatto -> Sfondo, poi Avatar, poi Sottotitoli
    final_clip = CompositeVideoClip([video] + avatar_layer + subtitle_clips)

    # 6. ESPORTAZIONE AD ALTA QUALITA'
    final_clip.write_videofile(
        output_path,
        fps=30,
        codec="libx264",
        audio_codec="aac",
        bitrate="8000k",
        preset="fast",
        threads=4
    )

# --- FLUSSO PRINCIPALE ---
def run():
    feed = feedparser.parse(RSS_URL)
    processed = get_processed_links()

    notizie_trovate = 0

    for entry in feed.entries:
        if entry.link not in processed:
            notizie_trovate += 1
            print(f"--- Lavoro sulla notizia: {entry.title} ---")

            prompt = f"""
            Analizza la notizia: {entry.title} - {entry.summary}.
            Crea uno script di 30 secondi.
            Restituisci SOLO un JSON con:
            "hook": Frase d'apertura breve e shock (max 10 parole).
            "voiceover": Il testo completo da leggere ad alta voce (incluso l'hook).
            "search_term": 1 o 2 parole chiave in INGLESE per cercare un video di sfondo (es. "technology", "space", "nature").
            """

            # Nuova sintassi per chiamare Gemini
            response = client.models.generate_content(
                model='gemini-3-flash-preview',
                contents=prompt
            )
            script_data = json.loads(clean_json(response.text))

            print(f"Script generato! Cerco video per: {script_data['search_term']}")

            if download_pexels_video(script_data['search_term']):
                print("Video di sfondo scaricato da Pexels.")
                asyncio.run(create_audio(script_data["voiceover"]))
                print("Audio generato, inizio il montaggio...")
                make_video(script_data["voiceover"], "audio.mp3", "background.mp4")
                print("VIDEO FINITO CON SUCCESSO!")
            else:
                print("Nessun video trovato su Pexels, salto la notizia.")

            save_link(entry.link)
            break

    if notizie_trovate == 0:
        print("NESSUNA NUOVA NOTIZIA NEL FEED. Nessun video generato in questo giro.")


if __name__ == "__main__":
    run()
