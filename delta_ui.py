# delta_ui.py
import threading
import customtkinter as ctk
from tkinter import scrolledtext
import time
import logging
import speech_recognition as sr
import delta  # your backend module (delta.py)

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")
logging.basicConfig(level=logging.INFO)

class DeltaApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Δ  Delta AI Assistant")
        self.geometry("1000x700")
        self.configure(fg_color="#050a14")

        # layout
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # state
        self.listening = False
        self._orig_speak = None
        self._bg_listener_stop = None  # function returned by listen_in_background
        self._sr_recognizer = None
        self._sr_mic = None

        # UI components
        self.create_header_frame()
        self.create_main_frame()
        self.create_footer_frame()

        # animation
        self.pulse_alpha = 0.0
        self.pulse_increasing = True
        self.animate_pulse()

        # monkeypatch delta.speak -> ui wrapper so UI shows spoken text
        self.after(100, self.initialize_delta)

        # ensure graceful close
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    # ---------------- UI creation ----------------
    def create_header_frame(self):
        header_frame = ctk.CTkFrame(self, fg_color="transparent")
        header_frame.grid(row=0, column=0, sticky="ew", pady=(18, 10))
        header_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(header_frame, text="[ • === • === • ]",
                     font=("Courier New", 14), text_color="#1e90ff").grid(row=0, column=0)
        self.header = ctk.CTkLabel(header_frame, text="Δ  DELTA NEURAL INTERFACE",
                                   font=("Orbitron", 32, "bold"),
                                   text_color="#00eaff")
        self.header.grid(row=1, column=0, pady=(4, 6))

    def create_main_frame(self):
        main_frame = ctk.CTkFrame(self, fg_color="transparent")
        main_frame.grid(row=1, column=0, sticky="nsew", padx=18, pady=10)
        main_frame.grid_rowconfigure(0, weight=1)
        main_frame.grid_columnconfigure(0, weight=1)

        self.chat_box = scrolledtext.ScrolledText(
            main_frame, wrap="word", bg="#070d1a", fg="#00ffff",
            font=("JetBrains Mono", 12), insertbackground="#00ffff",
            relief="flat", borderwidth=0, padx=10, pady=10
        )
        self.chat_box.grid(row=0, column=0, sticky="nsew")

        # initial status
        self._insert_to_chat("SYSTEM", 
"""╔════════════════════════════════════╗
║  DELTA AI SYSTEM v2.0              ║
║  Neural Interface Activated        ║
║  Quantum Core: Online              ║
╚════════════════════════════════════╝

🟢 Delta initialized and ready.
Say 'Hey Delta' or click the activation button below.
""")

    def create_footer_frame(self):
        footer_frame = ctk.CTkFrame(self, fg_color="transparent")
        footer_frame.grid(row=2, column=0, sticky="ew", pady=12)
        footer_frame.grid_columnconfigure(0, weight=1)

        btn_container = ctk.CTkFrame(footer_frame, fg_color="transparent")
        btn_container.grid(row=0, column=0)

        self.mic_button = ctk.CTkButton(
            btn_container, text="⦿", font=("Arial", 34),
            width=110, height=110, corner_radius=55,
            fg_color="#0a2642", hover_color="#1e90ff",
            command=self.manual_listen, border_width=2, border_color="#00eaff"
        )
        self.mic_button.pack(pady=6)

        status_frame = ctk.CTkFrame(footer_frame, fg_color="#0a1625", corner_radius=6,
                                   border_color="#1e90ff", border_width=1)
        status_frame.grid(row=1, column=0, pady=(8,0))
        self.status_label = ctk.CTkLabel(status_frame, text="■ SYSTEM IDLE ■",
                                         font=("JetBrains Mono", 13), text_color="#888")
        self.status_label.pack(padx=18, pady=8)

    # ---------------- utilities ----------------
    def _insert_to_chat(self, speaker, message):
        """Thread-safe insertion into chat (use self.after when called from background)."""
        def _do():
            self.chat_box.configure(state="normal")
            ts = time.strftime("%H:%M:%S")
            self.chat_box.insert("end", f"[{ts}] {speaker}: {message}\n\n")
            self.chat_box.see("end")
            self.chat_box.configure(state="disabled")
        # always schedule on main thread
        self.after(0, _do)

    # wrapper used on init so we can display delta's speak outputs in UI
    def _ui_speak_wrapper(self, text: str) -> str:
        """Call original speak and also display in UI chat."""
        # display immediately
        self._insert_to_chat("Δ Delta", text)
        # call original backend speak (which already does TTS in background)
        if self._orig_speak:
            try:
                return self._orig_speak(text)
            except Exception as e:
                logging.warning("backend speak failed: %s", e)
                return text
        return text

    # ---------------- initialization ----------------
    def initialize_delta(self):
        """Override speak, start passive wake listener."""
        # save and override delta.speak so UI displays everything delta speaks
        try:
            self._orig_speak = getattr(delta, "speak", None)
            # replace only if present
            if self._orig_speak:
                delta.speak = self._ui_speak_wrapper
        except Exception as e:
            logging.warning("Could not override delta.speak: %s", e)

        # show startup text (delta.greet_user uses delta.speak after fix)
        try:
            # run greet_user in background to avoid blocking UI
            threading.Thread(target=self._run_greet_user, daemon=True).start()
        except Exception as e:
            logging.warning("greet_user thread failed: %s", e)

        # start passive background wake listener
        try:
            self._start_passive_wake_listener()
        except Exception as e:
            logging.warning("Could not start passive listener: %s", e)
            self._insert_to_chat("SYSTEM", "Passive wake listener failed to start.")

    def _run_greet_user(self):
        try:
            # delta.greet_user() should call delta.speak internally
            if hasattr(delta, "greet_user"):
                delta.greet_user()
            else:
                # fallback greeting
                delta.speak("Hello. Delta is online.")
        except Exception as e:
            logging.warning("greet_user error: %s", e)

    # ---------------- manual listening ----------------
    def manual_listen(self):
        """Called by mic button: listen once and process."""
        # avoid reentrancy
        if self.listening:
            return
        self.listening = True
        self._insert_to_chat("SYSTEM", "Listening for your command...")
        self.status_label.configure(text="◉ LISTENING...", text_color="#00eaff")
        self.mic_button.configure(fg_color="#00eaff")

        def _task():
            try:
                cmd = delta.listen(timeout=6, phrase_time_limit=8)
                if not cmd:
                    self._insert_to_chat("SYSTEM", "No command detected.")
                else:
                    self._insert_to_chat("You", cmd)
                    try:
                        # process_command will call delta.speak (which now updates UI)
                        delta.process_command(cmd)
                    except Exception as e:
                        self._insert_to_chat("ERROR", f"Processing error: {e}")
            except Exception as e:
                self._insert_to_chat("ERROR", f"Listen failed: {e}")
            finally:
                # restore UI on main thread
                self.after(0, self._reset_listen_ui)

        threading.Thread(target=_task, daemon=True).start()

    def _reset_listen_ui(self):
        self.status_label.configure(text="■ SYSTEM IDLE ■", text_color="#888")
        self.mic_button.configure(fg_color="#0a2642")
        self.listening = False

    # ---------------- passive wake listener ----------------
    def _start_passive_wake_listener(self):
        """
        Start a background passive listener using speech_recognition.listen_in_background.
        callback will be invoked on recognition; we schedule UI activation on main thread.
        """
        try:
            recognizer = sr.Recognizer()
            mic = sr.Microphone()
            self._sr_recognizer = recognizer
            self._sr_mic = mic
        except Exception as e:
            self._insert_to_chat("SYSTEM", f"Microphone initialization failed: {e}")
            return

        def callback(recognizer_, audio):
            # this runs in background thread from speech_recognition
            try:
                phrase = ""
                try:
                    phrase = recognizer_.recognize_google(audio).lower()
                except sr.UnknownValueError:
                    return
                except sr.RequestError:
                    # network / google error; ignore silently (or log)
                    logging.debug("Google SR request error in passive listener.")
                    return

                if not phrase:
                    return
                logging.debug("Passive heard: %s", phrase)

                if "hey delta" in phrase or phrase.strip().startswith("delta"):
                    # schedule activation on main thread with the phrase
                    self.after(0, self._on_wake_detected, phrase)
            except Exception as e:
                logging.warning("Passive callback error: %s", e)

        # start listening in background
        try:
            stop_func = recognizer.listen_in_background(mic, callback, phrase_time_limit=4)
            self._bg_listener_stop = stop_func
            self._insert_to_chat("SYSTEM", "Passive wake listener started.")
        except Exception as e:
            self._insert_to_chat("SYSTEM", f"Wake listener failed to start: {e}")

    def _on_wake_detected(self, phrase: str):
        """Handle wake detection on main thread."""
        # avoid reentrancy
        if self.listening:
            return
        self._insert_to_chat("SYSTEM", "Wake word detected.")
        # remove wake phrase from text if present
        cmd = phrase
        if "hey delta" in cmd:
            cmd = cmd.replace("hey delta", "").strip()
        elif cmd.startswith("delta"):
            cmd = cmd[len("delta"):].strip()

        # show original phrase as You
        self._insert_to_chat("You", phrase)

        # set UI active
        self.listening = True
        self.status_label.configure(text="◉ NEURAL LINK ACTIVE ◉", text_color="#00eaff")
        self.mic_button.configure(fg_color="#00eaff")

        # If there's an immediate command after wake word, process it; otherwise listen again for the command
        def _handle_activation():
            try:
                if cmd:
                    delta.process_command(cmd)
                else:
                    # listen for the next phrase (follow-up)
                    more = delta.listen(timeout=6, phrase_time_limit=8)
                    if more:
                        self._insert_to_chat("You", more)
                        delta.process_command(more)
                    else:
                        self._insert_to_chat("SYSTEM", "No follow-up command heard.")
            except Exception as e:
                self._insert_to_chat("ERROR", f"Activation processing failed: {e}")
            finally:
                self._reset_listen_ui()

        threading.Thread(target=_handle_activation, daemon=True).start()

    # ---------------- animation ----------------
    def animate_pulse(self):
        # simple breathing effect, update mic color using pulse_alpha
        if self.pulse_increasing:
            self.pulse_alpha += 0.03
            if self.pulse_alpha >= 1:
                self.pulse_increasing = False
        else:
            self.pulse_alpha -= 0.03
            if self.pulse_alpha <= 0:
                self.pulse_increasing = True
        try:
            val = int(10 + 45 * self.pulse_alpha)
            hexcol = f"#{val:02x}2642"
            self.mic_button.configure(fg_color=hexcol)
        except Exception:
            pass
        self.after(55, self.animate_pulse)

    # ---------------- shutdown ----------------
    def on_close(self):
        # stop passive listener if running
        try:
            if self._bg_listener_stop:
                self._bg_listener_stop(wait_for_stop=False)
        except Exception:
            pass
        # restore original speak (optional)
        try:
            if self._orig_speak:
                delta.speak = self._orig_speak
        except Exception:
            pass
        # ask delta to say goodbye (non-blocking)
        try:
            delta.speak("Shutting down. Goodbye.")
        except Exception:
            pass
        # close UI
        self.destroy()


if __name__ == "__main__":
    app = DeltaApp()
    app.mainloop()
