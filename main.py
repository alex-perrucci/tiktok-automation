import os
import json
import re
import asyncio
import math
import requests
import feedparser
from google import genai
import edge_tts
import numpy as np  # <-- Aggiunto per analizzare le onde audio
from moviepy.editor import VideoFileClip, AudioFileClip, TextClip, CompositeVideoClip, CompositeAudioClip, ImageClip, \
    VideoClip
import PIL.Image

if not hasattr(PIL.Image, 'ANTIALIAS'):
    PIL.Image.ANTIALIAS = PIL.Image.LANCZOS
from moviepy.audio.fx.all import audio_loop, volumex
import time

# --- CONFIGURAZIONI ---
client = genai.Client()
PEXELS_KEY = os.environ.get("PEXELS_API_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

RSS_URL = "https://www.ilpost.it/internet/feed/"
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


def send_telegram_video(video_path, caption="Ecco il tuo nuovo video pronto per i social! 🚀"):
    print("Spedisco il video su Telegram...")
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendVideo"

    with open(video_path, 'rb') as video_file:
        payload = {'chat_id': TELEGRAM_CHAT_ID, 'caption': caption}
        files = {'video': video_file}
        response = requests.post(url, data=payload, files=files)

    if response.status_code == 200:
        print("✅ Video inviato con successo su Telegram!")
    else:
        print(f"❌ Errore nell'invio Telegram: {response.text}")


# --- VOCE VELOCIZZATA TIPO TIKTOK ---
async def create_audio(text, filename="audio.mp3"):
    voice = "it-IT-DiegoNeural"  # Voce più dinamica di Giuseppe
    # Aumento la velocità del 15% per dare più ritmo (fondamentale negli Shorts)
    communicate = edge_tts.Communicate(text, voice, rate="+15%")
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
    # 1. Carica le clip (Il video di sfondo viene velocizzato del 30% per meno noia visiva)
    video = VideoFileClip(bg_path).speedx(1.3)
    voice_audio = AudioFileClip(audio_path)

    # --- MIXAGGIO MUSICA DI SOTTOFONDO ---
    try:
        from moviepy.audio.fx.all import audio_loop, volumex
        print("Provo a caricare la musica di sottofondo...")
        bg_music = AudioFileClip("bg_music.mp3")
        bg_music = audio_loop(bg_music, duration=voice_audio.duration)
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

    # ==========================================
    # 🌟 V2.0: AVATAR PNGTUBER AUDIO-REATTIVO + ZOOM
    # ==========================================
    try:
        print("Genero l'Avatar Parlante V2.0...")

        # Carica facce aperte e chiuse
        img_chiusa = ImageClip("avatar_chiuso.png").resize(width=900)
        img_aperta = ImageClip("avatar_aperto.png").resize(width=900)

        def seleziona_faccia(t):
            try:
                # Legge il volume in quel millisecondo
                frame_audio = final_audio.get_frame(t)
                volume = abs(frame_audio[0]) if isinstance(frame_audio, np.ndarray) else abs(frame_audio)
                # Soglia del volume per aprire la bocca
                if volume > 0.02:
                    return img_aperta.get_frame(t)
                else:
                    return img_chiusa.get_frame(t)
            except:
                return img_chiusa.get_frame(t)

        avatar_parlante = VideoClip(seleziona_faccia, duration=video.duration)

        # Effetto Zoom (si ingrandisce lentamente)
        avatar_parlante = avatar_parlante.resize(lambda t: 1 + 0.015 * t)

        # Effetto fluttuazione in basso
        avatar_parlante = avatar_parlante.set_position(lambda t: ('center', 820 + 15 * math.sin(t * 3)))

        avatar_layer = [avatar_parlante]
        print("Avatar Parlante generato con successo!")
    except Exception as e:
        print(f"Errore Avatar: assicurati di avere avatar_chiuso.png e avatar_aperto.png. Errore: {e}")
        avatar_layer = []

    # ==========================================
    # 🌟 V2.0: SOTTOTITOLI 1 PAROLA STILE CAPCUT (Colori Alternati)
    # ==========================================
    words = full_text.split()
    time_per_word = final_audio.duration / len(words)
    subtitle_clips = []
    current_time = 0

    # Sequenza di colori ad alto impatto (Giallo, Bianco, Verde Fluo, Bianco)
    colori_dinamici = ['yellow', 'white', '#00FF00', 'white']

    for i, word in enumerate(words):
        chunk_duration = time_per_word
        colore_scelto = colori_dinamici[i % len(colori_dinamici)]

        txt_clip = TextClip(
            word.upper(),  # Tutto maiuscolo per leggibilità immediata
            fontsize=115,  # Carattere gigante per 1 parola
            color=colore_scelto,
            font='Arial-Bold',
            stroke_color='black',
            stroke_width=5.0,  # Contorno spesso per staccare dal fondo
            method='caption',
            size=(1000, None),
            align='center'
        )

        txt_clip = txt_clip.set_position(('center', 1320)) \
            .set_start(current_time) \
            .set_duration(chunk_duration)

        subtitle_clips.append(txt_clip)
        current_time += chunk_duration

    # 5. Composizione
    final_clip = CompositeVideoClip([video] + avatar_layer + subtitle_clips)

    # 6. ESPORTAZIONE
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

    # FILTRO PAROLE VIETATE GLOBALE
    parole_vietate = ["offerte", "sconti", "amazon", "migliori", "recensione",
                      "guerra", "morto", "morta", "suicidio", "autolesionismo", "violenza",
                      "armi", "israele", "iran", "ucraina", "russia", "omicidio", "abusi",
                      "calcio", "serie a", "linkedin"]

    for entry in feed.entries:
        titolo_lower = entry.title.lower()

        # 1. Controllo Spam/Ban
        if any(parola in titolo_lower for parola in parole_vietate):
            print(f"🚫 Salto notizia commerciale o sensibile: {entry.title}")
            save_link(entry.link)
            continue

        # 2. Controllo Duplicati
        if entry.link in processed:
            continue

        notizie_trovate += 1
        print(f"--- Lavoro sulla notizia: {entry.title} ---")

        prompt = f"""
                    Sei un content creator virale su TikTok e YouTube Shorts. Il tuo stile è diretto, sarcastico, emotivo e senza peli sulla lingua. Odii il linguaggio noioso da telegiornale o da Wikipedia.
                    Analizza questa notizia: {entry.title} - {entry.summary}.

                    Crea uno script dinamico e ritmato di circa 30 secondi (massimo 70-80 parole).

                    REGOLE DI STILE:
                    1. Tono: Sii sarcastico, indignato, scioccato o iper-entusiasta. Esprimi un'opinione forte sulla notizia. Parla come se stessi svelando uno scandalo a un amico.
                    2. Linguaggio: Usa frasi brevi e taglienti. Dai sempre del "tu" o del "voi" allo spettatore. Usa parole a forte impatto emotivo (follia, assurdo, pazzesco, truffa, geniale).
                    3. Chiusura: Il "voiceover" deve SEMPRE finire con una domanda provocatoria per far commentare la gente (es. "Voi che ne pensate?", "Siete d'accordo?", "Follia o genio? Fatemelo sapere sotto!").

                    Restituisci SOLO ed ESCLUSIVAMENTE un JSON valido con questa esatta struttura, senza nient'altro:
                    {{
                        "hook": "Frase d'apertura super provocatoria e shock che blocca lo scroll (massimo 10 parole).",
                        "voiceover": "Il testo completo da leggere ad alta voce. DEVE iniziare con la frase dell'hook e finire con la domanda provocatoria.",
                        "search_term": "1 o 2 parole chiave in INGLESE per cercare il video di sfondo perfetto (es. 'hacker', 'money', 'angry', 'technology')."
                    }}
                    """

        # SISTEMA ANTI-CRASH GEMINI
        massimo_tentativi = 3
        for tentativo in range(massimo_tentativi):
            try:
                response = client.models.generate_content(
                    model='gemini-3-flash-preview',
                    contents=prompt
                )
                break
            except Exception as e:
                print(f"Errore Gemini (Tentativo {tentativo + 1}/{massimo_tentativi}): {e}")
                if tentativo < massimo_tentativi - 1:
                    print("Aspetto 30 secondi e riprovo...")
                    time.sleep(30)
                else:
                    print("Gemini è intasato. Salto questa notizia per ora.")
                    response = None

        if not response:
            continue

        script_data = json.loads(clean_json(response.text))

        print(f"Script generato! Cerco video per: {script_data['search_term']}")

        if download_pexels_video(script_data['search_term']):
            print("Video di sfondo scaricato da Pexels.")
            asyncio.run(create_audio(script_data["voiceover"]))
            print("Audio generato, inizio il montaggio...")
            make_video(script_data["voiceover"], "audio.mp3", "background.mp4")
            print("VIDEO FINITO CON SUCCESSO!")
            send_telegram_video("final_video.mp4", caption=f"Notizia: {entry.title}\n\nPronto per essere pubblicato! 📱")
        else:
            print("Nessun video trovato su Pexels, salto la notizia.")

        save_link(entry.link)
        break  # Finito il primo video valido, esce dal loop

    if notizie_trovate == 0:
        print("NESSUNA NUOVA NOTIZIA NEL FEED. Nessun video generato in questo giro.")


if __name__ == "__main__":
    run()