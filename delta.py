# delta.py -- Delta assistant backend (UI-friendly)
import os
import json
import time
import threading
import datetime
import logging
import requests
import smtplib
import ast
import math
import re
import operator as op
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# voice / recognition
import pyttsx3
import speech_recognition as sr
import wikipedia
import pywhatkit
import pyjokes
import webbrowser

# ---------- Logging ----------
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

# ---------- Config / Keys (use env vars if available) ----------
MEMORY_FILE = os.environ.get("DELTA_MEMORY_FILE", "delta_memory.json")
NEWS_API_KEY = os.environ.get("DELTA_NEWS_API_KEY", "0530443142104c65965ccb122f9395a4")      # optional
WEATHER_API_KEY = os.environ.get("DELTA_WEATHER_API_KEY", "a201655e97c349c993883936250411")# optional (WeatherAPI)
EMAIL_SENDER = os.environ.get("DELTA_EMAIL_SENDER", "")      # optional
EMAIL_PASSWORD = os.environ.get("DELTA_EMAIL_PASSWORD", "")  # optional

if not NEWS_API_KEY:
    logging.warning("DELTA_NEWS_API_KEY not set — news feature disabled until configured.")
if not WEATHER_API_KEY:
    logging.warning("DELTA_WEATHER_API_KEY not set — weather feature disabled until configured.")
if not EMAIL_SENDER or not EMAIL_PASSWORD:
    logging.warning("Email sender/password not set — email feature disabled until configured.")

WAKE_WORDS = ["hey delta", "delta"]

# ---------- TTS and recognizer setup ----------
_engine = pyttsx3.init()
_voices = _engine.getProperty("voices")
if len(_voices) > 1:
    try:
        _engine.setProperty("voice", _voices[1].id)  # prefer female if available
    except Exception:
        pass
_engine.setProperty("rate", 170)

_speak_lock = threading.Lock()

_recognizer = sr.Recognizer()
# Note: microphone will be opened per-listen to avoid locking resource error on some systems

# ---------- Memory helpers ----------
def _load_memory():
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _save_memory(mem):
    try:
        with open(MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump(mem, f, indent=2)
    except Exception as e:
        logging.warning("Failed to save memory: %s", e)

memory = _load_memory()

# ---------- Speak (UI-friendly) ----------
def speak(text: str) -> str:
    """
    Queue text for TTS in background and return the text so the UI can display it.
    Uses a lock to avoid overlapping runAndWait calls.
    """
    if not text:
        return ""
    def _tts(t):
        try:
            with _speak_lock:
                _engine.say(t)
                _engine.runAndWait()
        except Exception as e:
            logging.warning("TTS failed: %s", e)
    threading.Thread(target=_tts, args=(text,), daemon=True).start()
    logging.info("Delta (speech queued): %s", text)
    return text

# ---------- Listen (UI-friendly) ----------
def listen(timeout: int = 5, phrase_time_limit: int = 8) -> str:
    """
    Listen once and return recognized text (lowercased). On errors returns empty string.
    Designed to be called from a background thread (so it won't block UI mainloop).
    """
    try:
        with sr.Microphone() as source:
            _recognizer = sr.Recognizer()
            _recognizer.adjust_for_ambient_noise(source, duration=0.5)
            audio = _recognizer.listen(source, timeout=timeout, phrase_time_limit=phrase_time_limit)
        try:
            text = _recognizer.recognize_google(audio)
            return text.lower()
        except sr.UnknownValueError:
            return ""
        except sr.RequestError:
            # network problems
            speak("Network error during speech recognition.")
            return ""
    except Exception as e:
        # microphone unavailable or other error — return empty so UI can fallback to typed input
        logging.debug("Microphone/listen failed: %s", e)
        return ""

def greet_user():
    """UI-friendly greeting that uses delta.speak() so UI displays the message too."""
    hour = datetime.datetime.now().hour
    if 0 <= hour < 12:
        speak_text = "Good morning! I'm Delta, your personal AI assistant."
    elif 12 <= hour < 18:
        speak_text = "Good afternoon! I'm Delta, your personal AI assistant."
    else:
        speak_text = "Good evening! I'm Delta, your personal AI assistant."
    # use the speak() function (which is overridden by UI to also display text)
    speak(speak_text)


# ---------- Safe math evaluator ----------
ALLOWED_OPERATORS = {
    ast.Add: op.add, ast.Sub: op.sub, ast.Mult: op.mul, ast.Div: op.truediv,
    ast.Pow: op.pow, ast.Mod: op.mod, ast.FloorDiv: op.floordiv, ast.USub: op.neg
}
SAFE_NAMES = {k: getattr(math, k) for k in ("sin","cos","tan","sqrt","log","log10","ceil","floor","factorial","fabs")}
SAFE_NAMES.update({"pi": math.pi, "e": math.e})

def _safe_eval(expr: str):
    try:
        node = ast.parse(expr, mode="eval").body
        return _eval_node(node)
    except Exception:
        raise ValueError("Invalid expression")

def _eval_node(node):
    if isinstance(node, ast.Constant):  # py3.8+
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError("Invalid constant")
    if isinstance(node, ast.Num):
        return node.n
    if isinstance(node, ast.BinOp):
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        op_type = type(node.op)
        if op_type in ALLOWED_OPERATORS:
            return ALLOWED_OPERATORS[op_type](left, right)
    if isinstance(node, ast.UnaryOp):
        op_type = type(node.op)
        if op_type in ALLOWED_OPERATORS:
            return ALLOWED_OPERATORS[op_type](_eval_node(node.operand))
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name) and node.func.id in SAFE_NAMES:
            args = [_eval_node(a) for a in node.args]
            return SAFE_NAMES[node.func.id](*args)
    if isinstance(node, ast.Name):
        if node.id in SAFE_NAMES:
            return SAFE_NAMES[node.id]
    raise ValueError("Unsupported expression")

# ---------- Core features (return strings for UI) ----------
def tell_time() -> str:
    t = datetime.datetime.now().strftime("%I:%M %p")
    return speak(f"The current time is {t}")

def tell_date() -> str:
    d = datetime.datetime.now().strftime("%B %d, %Y")
    return speak(f"Today's date is {d}")

def search_wikipedia(command: str) -> str:
    topic = command.replace("wikipedia", "").strip()
    if not topic:
        # UI should ask user for topic; here just return prompt
        return speak("What should I search on Wikipedia?")
    speak("Searching Wikipedia...")
    try:
        info = wikipedia.summary(topic, sentences=2)
        return speak(info)
    except Exception as e:
        logging.debug("Wikipedia error: %s", e)
        return speak("Couldn't find information on Wikipedia.")

def search_google(command: str) -> str:
    query = command.replace("search", "").replace("google", "").strip()
    if not query:
        return speak("What should I search for?")
    # Launch search in background thread to avoid blocking
    threading.Thread(target=lambda q=query: pywhatkit.search(q), args=(), daemon=True).start()
    return speak(f"Searching Google for {query}")

def open_app_or_website(command: str) -> str:
    cmd = command.lower()
    if "youtube" in cmd:
        threading.Thread(target=lambda: webbrowser.open("https://www.youtube.com"), daemon=True).start()
        return speak("Opening YouTube.")
    if "google" in cmd:
        threading.Thread(target=lambda: webbrowser.open("https://www.google.com"), daemon=True).start()
        return speak("Opening Google.")
    if "notepad" in cmd:
        try:
            os.system("notepad")
            return speak("Opening Notepad.")
        except Exception:
            return speak("Unable to open Notepad.")
    if "chrome" in cmd:
        try:
            os.startfile("C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe")
            return speak("Opening Chrome.")
        except Exception:
            return speak("Unable to open Chrome. Please check the path.")
    # generic open
    if cmd.startswith("open "):
        target = command.replace("open ", "").strip()
        if target and "." in target and not target.startswith("http"):
            target = "http://" + target
        if target:
            threading.Thread(target=lambda url=target: webbrowser.open(url), daemon=True).start()
            return speak(f"Opening {target}")
    return speak("I can't open that yet.")

def tell_joke() -> str:
    return speak(pyjokes.get_joke())

# ---------- Weather ----------
def get_weather(city: str = "Delhi") -> str:
    if not WEATHER_API_KEY:
        return speak("Weather is not configured. Set DELTA_WEATHER_API_KEY to enable weather.")
    try:
        url = f"http://api.weatherapi.com/v1/current.json?key={WEATHER_API_KEY}&q={city}&aqi=no"
        r = requests.get(url, timeout=6)
        data = r.json()
        if "current" in data:
            cond = data["current"]["condition"]["text"]
            temp = data["current"]["temp_c"]
            hum = data["current"]["humidity"]
            wind = data["current"]["wind_kph"]
            return speak(f"The weather in {city} is {cond}, temperature {temp}°C, humidity {hum}%, wind {wind} kph.")
        if "error" in data:
            return speak(data["error"].get("message", "Unknown location."))
        return speak("Couldn't fetch weather.")
    except Exception as e:
        logging.debug("Weather error: %s", e)
        return speak("Weather lookup failed.")

# ---------- News ----------
def get_news() -> str:
    if not NEWS_API_KEY:
        return speak("News is not configured. Set DELTA_NEWS_API_KEY to enable headlines.")
    try:
        url = f"https://newsapi.org/v2/top-headlines?country=in&apiKey={NEWS_API_KEY}"
        data = requests.get(url, timeout=6).json()
        if data.get("status") == "ok":
            items = data.get("articles", [])[:5]
            if not items:
                return speak("No news found.")
            # Speak headlines one by one (also return a summary string)
            speak("Here are the top headlines:")
            headlines = []
            for i, a in enumerate(items, 1):
                title = a.get("title", "No title")
                headlines.append(f"{i}. {title}")
                speak(f"{i}. {title}")
            # return joined headlines for UI display
            return "\n".join(headlines)
        return speak("Could not fetch news.")
    except Exception as e:
        logging.debug("News error: %s", e)
        return speak("News lookup failed.")

# ---------- Email ----------
def send_email(to: str, subject: str, body: str) -> str:
    if not EMAIL_SENDER or not EMAIL_PASSWORD:
        return speak("Email not configured. Set DELTA_EMAIL_SENDER and DELTA_EMAIL_PASSWORD to enable sending.")
    try:
        msg = MIMEMultipart()
        msg["From"] = EMAIL_SENDER
        msg["To"] = to
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))
        server = smtplib.SMTP("smtp.gmail.com", 587, timeout=10)
        server.starttls()
        server.login(EMAIL_SENDER, EMAIL_PASSWORD)
        server.send_message(msg)
        server.quit()
        return speak("Email sent successfully.")
    except Exception as e:
        logging.warning("Email failed: %s", e)
        return speak("Unable to send the email right now.")

# ---------- Reminders / Alarms ----------
def _alarm_thread(alarm_time: str, message: str):
    speak(f"Alarm set for {alarm_time}.")
    while True:
        now = datetime.datetime.now().strftime("%H:%M")
        if now == alarm_time:
            speak(message)
            break
        time.sleep(10)

def set_alarm(command: str) -> str:
    m = re.search(r"(\d{1,2}:\d{2})", command)
    if m:
        alarm_time = m.group(1)
        alarm_time = f"{int(alarm_time.split(':')[0]):02d}:{int(alarm_time.split(':')[1]):02d}"
        threading.Thread(target=_alarm_thread, args=(alarm_time, "Alarm ringing"), daemon=True).start()
        return speak(f"Alarm set for {alarm_time}")
    return speak("Please tell me the alarm time in HH:MM format.")

def set_reminder(command: str) -> str:
    m = re.search(r"remind me to (.+?) in (\d+)\s*minutes?", command)
    if m:
        action = m.group(1).strip()
        minutes = int(m.group(2))
        def _rem():
            speak(f"Reminder set for {minutes} minutes from now.")
            time.sleep(minutes * 60)
            speak(f"Reminder: {action}")
        threading.Thread(target=_rem, daemon=True).start()
        return speak(f"Reminder set for {minutes} minutes from now.")
    return speak("Please say reminders like: remind me to buy milk in 10 minutes.")

# ---------- Definitions ----------
def get_definition(word: str) -> str:
    try:
        url = f"https://api.dictionaryapi.dev/api/v2/entries/en/{word}"
        data = requests.get(url, timeout=6).json()
        if isinstance(data, list) and data:
            defs = data[0].get("meanings", [])[0].get("definitions", [])
            if defs:
                return speak(defs[0].get("definition"))
        return speak("I couldn't find a definition.")
    except Exception as e:
        logging.debug("Definition error: %s", e)
        return speak("Definition lookup failed.")

# ---------- Command handler (returns textual reply for UI) ----------
def process_command(command: str) -> str:
    """
    Process a command string and return the main reply string.
    The function will call speak() to vocalize responses but will also return text so UI can display it.
    """
    if not command:
        return speak("I didn't hear anything. Please say that again or type your command.")

    cmd = command.lower().strip()

    # Name setting
    if cmd.startswith("call me ") or cmd.startswith("my name is "):
        name = cmd.replace("call me ", "").replace("my name is ", "").strip()
        memory["name"] = name.capitalize()
        _save_memory(memory)
        return speak(f"Okay, I'll call you {memory['name']}.")

    # Basic info
    if "time" in cmd and not cmd.startswith("time in"):
        return tell_time()
    if "date" in cmd:
        return tell_date()

    # wikipedia
    if "wikipedia" in cmd:
        return search_wikipedia(cmd)

    # search / google
    if cmd.startswith("search ") or "google" in cmd:
        return search_google(cmd)

    # open
    if "open" in cmd:
        return open_app_or_website(cmd)

    # weather (ask UI to provide city if necessary)
    if "weather" in cmd:
        # extract city if present: "weather in mumbai"
        m = re.search(r"weather(?: in| at)? ([a-zA-Z\s]+)", cmd)
        if m:
            city = m.group(1).strip()
        else:
            return speak("Which city would you like the weather for?")
        return get_weather(city)

    # news
    if "news" in cmd:
        return get_news()

    # email
    if "email" in cmd:
        # for UI integration, UI should gather recipient/subject/body and call send_email()
        return speak("To send an email, please provide recipient, subject and message in the UI.")

    # jokes
    if "joke" in cmd:
        return tell_joke()

    # identity
    if "your name" in cmd or "who are you" in cmd:
        return speak("I am Delta, your personal AI assistant.")

    if "my name" in cmd:
        return speak(f"Your name is {memory.get('name', 'not set yet')}.")

    # exit / stop
    if any(w in cmd for w in ("exit", "stop", "quit", "goodbye")):
        return speak("Goodbye. Shutting down.")  # let UI decide to close the app

    # math
    if "calculate" in cmd or any(ch in cmd for ch in "+-*/%^") or cmd.startswith("what is"):
        expr = cmd.replace("calculate", "").replace("what is", "").strip()
        try:
            result = _safe_eval(expr)
            return speak(f"The result is {result}")
        except Exception:
            return speak("I couldn't calculate that.")

    # alarm / reminder
    if "set alarm" in cmd or "alarm" in cmd:
        return set_alarm(cmd)
    if "remind me" in cmd:
        return set_reminder(cmd)

    # definition
    if cmd.startswith("define ") or "definition of" in cmd:
        word = cmd.replace("define ", "").replace("definition of", "").strip()
        if word:
            return get_definition(word)
        return speak("Which word should I define?")

    # play youtube
    if "play" in cmd and "youtube" in cmd:
        q = cmd.replace("play", "").replace("on youtube", "").strip()
        if q:
            threading.Thread(target=lambda: pywhatkit.playonyt(q), daemon=True).start()
            return speak(f"Playing {q} on YouTube.")
        return speak("What should I play?")

    # fallback: ask to search the web
    speak("I didn't understand that. Should I search the web for it?")
    # Note: UI should capture the user's follow up; here we just return the prompt
    return "I didn't understand that. Say 'yes' to search the web."

# ---------- Small utilities for UI ----------
def delta_listen_once(timeout: int = 5, phrase_time_limit: int = 8) -> str:
    """Wrapper for listen() for UI use (keeps naming clear)."""
    return listen(timeout=timeout, phrase_time_limit=phrase_time_limit)

def delta_process(command: str) -> str:
    """Wrapper around process_command for UI imports."""
    return process_command(command)

# ---------- If run directly as script (optional) ----------
if __name__ == "__main__":
    # minimal interactive console run for debugging (not used by UI)
    speak("Delta starting in console mode.")
    try:
        while True:
            txt = listen(timeout=6, phrase_time_limit=6)
            if not txt:
                continue
            resp = process_command(txt)
            time.sleep(0.2)
    except KeyboardInterrupt:
        speak("Goodbye.")
