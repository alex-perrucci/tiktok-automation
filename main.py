import os
import json
import re
import asyncio
import math
import requests
import feedparser
import random
from google import genai
import edge_tts
import numpy as np
from moviepy.editor import VideoFileClip, AudioFileClip, TextClip, CompositeVideoClip, CompositeAudioClip, ImageClip, VideoClip, ColorClip
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

RSS_URLS = [
    "https://www.ilpost.it/internet/feed/",
    "https://www.google.it/alerts/feeds/01389272250505533510/7146043719198930595",
    "https://www.google.it/alerts/feeds/01389272250505533510/18046864339030224203",
    "https://www.google.it/alerts/feeds/01389272250505533510/8259819412184036855",
    "https://www.google.it/alerts/feeds/01389272250505533510/14945071228994360231",
    "https://www.google.it/alerts/feeds/01389272250505533510/14945071228994361854",
    "https://www.google.it/alerts/feeds/01389272250505533510/5464753421936836102",
    "https://www.google.it/alerts/feeds/01389272250505533510/3689173126201762454",
    "https://www.google.it/alerts/feeds/01389272250505533510/1013462294344154884",
    "https://www.google.it/alerts/feeds/01389272250505533510/13904276865364984014",
    "https://www.google.it/alerts/feeds/01389272250505533510/13904276865364986900",
    "https://www.google.it/alerts/feeds/01389272250505533510/1013462294344153198",
    "https://www.google.it/alerts/feeds/01389272250505533510/11625453670672590453",
    "https://www.google.it/alerts/feeds/01389272250505533510/5824630404075713593",
    "https://www.google.it/alerts/feeds/01389272250505533510/17424155088677675112",
    "https://www.google.it/alerts/feeds/01389272250505533510/17424155088677676098",
    "https://www.google.it/alerts/feeds/01389272250505533510/4901070992315156615",
    "https://www.google.it/alerts/feeds/01389272250505533510/1885785607556095889",
    "https://www.google.it/alerts/feeds/01389272250505533510/1885785607556093508",
    "https://www.google.it/alerts/feeds/01389272250505533510/3196451576645760734",
    "https://www.google.it/alerts/feeds/01389272250505533510/1002976599006865477",
    "https://www.google.it/alerts/feeds/01389272250505533510/13204700885184800141"
]
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
    voice = "it-IT-DiegoNeural"
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
    # 1. Sfondo velocizzato del 30% per ritmo visivo
    video = VideoFileClip(bg_path).speedx(1.3)
    voice_audio = AudioFileClip(audio_path)

    # --- MIXAGGIO MUSICA DI SOTTOFONDO ---
    try:
        print("Provo a caricare la musica di sottofondo...")
        bg_music = AudioFileClip("bg_music.mp3")
        bg_music = audio_loop(bg_music, duration=voice_audio.duration)
        bg_music = volumex(bg_music, 0.15)
        final_audio = CompositeAudioClip([voice_audio, bg_music])
        print("Musica mixata con successo!")
    except Exception as e:
        print("ATTENZIONE: Impossibile usare la musica. Usa solo la voce.")
        final_audio = voice_audio

    # 2. Loop o Taglio
    if video.duration < final_audio.duration:
        video = video.loop(duration=final_audio.duration)
    else:
        video = video.subclip(0, final_audio.duration)
    video = video.set_audio(final_audio)

    # 3. FORZATURA TIKTOK (9:16)
    target_ratio = 9 / 16
    current_ratio = video.w / video.h
    if current_ratio > target_ratio:
        new_width = int(video.h * target_ratio)
        video = video.crop(x_center=video.w / 2, y_center=video.h / 2, width=new_width, height=video.h)
    else:
        new_height = int(video.w / target_ratio)
        video = video.crop(x_center=video.w / 2, y_center=video.h / 2, width=video.w, height=new_height)
    video = video.resize(newsize=(1080, 1920))

    # 🔥 NUOVO: LIVELLO CINEMATICO SCURO (Migliora contrasto di avatar e scritte)
    dark_overlay = ColorClip(size=(1080, 1920), color=(0, 0, 0)).set_opacity(0.35).set_duration(video.duration)

    # ==========================================
    # 🌟 V3.0: AVATAR CON RIMBALZO ATTIVO E LIP-SYNC PERFETTO
    # ==========================================
    try:
        print("Genero l'Avatar Parlante PRO...")
        frame_chiuso = ImageClip("avatar_chiuso.png").resize(width=850).get_frame(0)
        frame_aperto = ImageClip("avatar_aperto.png").resize(width=850).get_frame(0)

        # Muove la bocca
        def seleziona_faccia(t):
            try:
                if t >= final_audio.duration: return frame_chiuso
                frame_audio = final_audio.get_frame(t)
                volume = abs(frame_audio[0]) if isinstance(frame_audio, np.ndarray) else abs(frame_audio)
                return frame_aperto if volume > 0.05 else frame_chiuso
            except:
                return frame_chiuso

        avatar_parlante = VideoClip(seleziona_faccia, duration=video.duration).set_mask(None)

        # 🔥 NUOVO: RIMBALZO ATTIVO (L'avatar "salta" quando parla forte)
        def posizione_dinamica(t):
            base_y = 1050 + 15 * math.sin(t * 3) # Respiro base
            try:
                if t < final_audio.duration:
                    frame_audio = final_audio.get_frame(t)
                    volume = abs(frame_audio[0]) if isinstance(frame_audio, np.ndarray) else abs(frame_audio)
                    if volume > 0.05:
                        return ('center', base_y - 15) # Micro-scatto verso l'alto
            except: pass
            return ('center', base_y)

        avatar_parlante = avatar_parlante.set_position(posizione_dinamica)
        avatar_layer = [avatar_parlante]
        print("Avatar Parlante generato con successo!")
    except Exception as e:
        print(f"Errore Avatar: {e}")
        avatar_layer = []

    # ==========================================
    # 🌟 V3.0: SOTTOTITOLI PROPORZIONALI CON FADE-IN
    # ==========================================
    words = full_text.split()
    total_chars = sum(len(word) for word in words)
    subtitle_clips = []
    current_time = 0
    colori_dinamici = ['yellow', 'white', '#00FF00', 'white']

    for i, word in enumerate(words):
        chunk_duration = final_audio.duration * (len(word) / total_chars)
        colore_scelto = colori_dinamici[i % len(colori_dinamici)]

        # 🔥 NUOVO: Pulizia testo (rimuove virgole e punti finali per un look più pulito)
        clean_word = re.sub(r'[,.;]+$', '', word)

        txt_clip = TextClip(
            clean_word.upper(),
            fontsize=95,
            color=colore_scelto,
            font='Arial-Bold',
            stroke_color='black',
            stroke_width=4.0,
            method='caption',
            size=(950, None),
            align='center'
        )

        # 🔥 NUOVO: crossfadein(0.05) crea l'effetto pop-up morbido di CapCut
        txt_clip = txt_clip.set_position(('center', 1250)) \
            .set_start(current_time) \
            .set_duration(chunk_duration) \
            .crossfadein(0.05)

        subtitle_clips.append(txt_clip)
        current_time += chunk_duration

    # 5. Composizione finale (Sfondo + Filtro Scuro + Avatar + Testo)
    final_clip = CompositeVideoClip([video, dark_overlay] + avatar_layer + subtitle_clips)

    # 6. 🔥 NUOVO: ESPORTAZIONE A 60 FPS (Super Fluidità)
    final_clip.write_videofile(
        output_path,
        fps=60, # <-- MAGIC HAPPENS HERE
        codec="libx264",
        audio_codec="aac",
        bitrate="8000k",
        preset="fast",
        threads=4
    )


# --- FLUSSO PRINCIPALE ---
def run():
    processed = get_processed_links()
    notizie_trovate = 0
    video_generato = False

    random.shuffle(RSS_URLS)

    for url_feed in RSS_URLS:
        if video_generato: break

        print(f"\n📡 Controllo il feed: {url_feed}")
        feed = feedparser.parse(url_feed)

        for entry in feed.entries:
            if entry.link not in processed:

                # --- FILTRO ANTI-BAN ---
                parole_vietate = ["offerte", "sconti", "amazon", "migliori", "recensione", "guerra", "morto", "morta",
                                  "suicidio", "violenza", "armi", "israele", "iran", "ucraina", "russia"]
                titolo_lower = entry.title.lower()

                if any(parola in titolo_lower for parola in parole_vietate):
                    print(f"🚫 Salto notizia vietata: {entry.title}")
                    save_link(entry.link)
                    continue

                notizie_trovate += 1
                print(f"✅ TROVATA! Lavoro sulla notizia: {entry.title}")

                prompt = f"""
Sei un content creator virale su TikTok e YouTube Shorts. Il tuo stile è diretto, sarcastico, emotivo e senza peli sulla lingua. Odii il linguaggio noioso.
Analizza questa notizia: {entry.title} - {entry.summary}.

Crea uno script dinamico e ritmato di circa 30 secondi (massimo 70-80 parole).

REGOLE DI STILE:
1. Tono: Sii sarcastico, indignato o scioccato.
2. Linguaggio: Usa frasi brevi. Dai del "tu" o "voi". Usa parole forti.
3. Chiusura: Finisci SEMPRE con una domanda provocatoria (es. "Voi che ne pensate?").

Restituisci SOLO un JSON valido con questa struttura:
{{
    "hook": "Frase d'apertura shock (max 10 parole).",
    "voiceover": "Il testo completo.",
    "search_term": "1-2 parole in INGLESE per cercare il video di sfondo."
}}
"""
                massimo_tentativi = 3
                response = None
                for tentativo in range(massimo_tentativi):
                    try:
                        response = client.models.generate_content(
                            model='gemini-3-flash-preview',
                            contents=prompt
                        )
                        break
                    except Exception as e:
                        print(f"Errore Gemini (Tentativo {tentativo + 1}): {e}")
                        if tentativo < massimo_tentativi - 1: time.sleep(30)

                if not response: continue

                try:
                    script_data = json.loads(clean_json(response.text))
                except Exception as e:
                    print(f"Errore JSON: {e}")
                    continue

                print(f"Script generato! Cerco video per: {script_data['search_term']}")

                if download_pexels_video(script_data['search_term']):
                    print("Video di sfondo scaricato.")
                    asyncio.run(create_audio(script_data["voiceover"]))
                    print("Audio generato, inizio il montaggio a 60 FPS...")

                    make_video(script_data["voiceover"], "audio.mp3", "background.mp4")

                    print("VIDEO FINITO CON SUCCESSO!")
                    send_telegram_video("final_video.mp4", caption=f"Notizia: {entry.title}\n\nPronto per i social! 📱")
                else:
                    print("Nessun video trovato su Pexels, salto la notizia.")

                save_link(entry.link)
                video_generato = True
                break

        if video_generato: break

    if notizie_trovate == 0:
        print("NESSUNA NUOVA NOTIZIA INTERESSANTE NEI FEED.")

if __name__ == "__main__":
    run()