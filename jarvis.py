import speech_recognition as sr
import pyttsx3
import datetime
import sys
import json
import os
import time
import urllib.request
import requests
from pathlib import Path
import re
import urllib.parse
from bs4 import BeautifulSoup
import webbrowser

# === Leviton ===
try:
    from decora_wifi import DecoraWiFiSession
    from decora_wifi.models.residential_account import ResidentialAccount
    LEVITON_AVAILABLE = True
except ImportError:
    LEVITON_AVAILABLE = False

# === Ring (official async method from docs) ===
try:
    from ring_doorbell import Auth, AuthenticationError, Requires2FAError, Ring
    RING_AVAILABLE = True
except ImportError:
    RING_AVAILABLE = False

class AI:
    def __init__(self):
        self.levitonlogin_ok = False
        self.ringlogin_ok = False
        
        self.r = sr.Recognizer()
        self.mic = sr.Microphone()
        
        self.config = self.load_config()
        self.user_name = self.config.get("user_name", "Sir")
        
        # ←←← ADD THESE 3 LINES HERE ←←←
        self.leviton_session = None
        self.leviton_switches = {}      # ← Fixes the main crash
        self.ring_devices = []          # ← Prevents future Ring crash
        
        self.login_to_leviton()
        self.login_ring_official_async()
        
        self.loaded()

    def speak(self, text):
        engine = pyttsx3.init()  # create a fresh engine each time
        engine.setProperty('rate', 155)
        engine.setProperty('volume', 1.0)
        for v in engine.getProperty('voices'):
            if 'david' in v.name.lower():
                engine.setProperty('voice', v.id)
                break
        engine.say(text)
        engine.runAndWait()
        engine.stop()  # release resources
    
    
    # ====== BACKGROUND LISTENING ======
    def start_background_listen(self):
        # Adjust for ambient noise once at startup
        with self.mic as source:
            print("Calibrating microphone for ambient noise...")
            self.r.adjust_for_ambient_noise(source, duration=3)
            self.r.dynamic_energy_threshold = True
            print("Calibration done!")

        # Define the callback for background listening
        def callback(recognizer, audio):
            try:
                q = recognizer.recognize_google(audio)
                print(f"You: {q}")
                if "jarvis" in q.lower():
                    cmd = q.lower().replace("jarvis", "").strip()
                    if cmd:
                        self.respond(cmd)
                    else:
                        self.speak("Yes, Sir?")
            except sr.UnknownValueError:
                # Could not understand audio, ignore
                pass
            except Exception as e:
                print(f"Speech recognition error: {e}")

        # Start background listening
        self.background_listening = self.r.listen_in_background(
            self.mic, callback, phrase_time_limit=20
        )
        print("Jarvis is now listening in the background...")

    # ====== STOP BACKGROUND LISTENING ======
    def stop_background_listen(self):
        if self.background_listening:
            self.background_listening(wait_for_stop=False)
            print("Background listening stopped.")
    
    def load_config(self):
        try:
            with open("config.json") as f:
                return json.load(f)
        except Exception as e:
            input(f"Could not load file: {e}")
            sys.exit(1)


    # ================== LEVITON CONTROL ==================
    def login_to_leviton(self):
        try:
            email = self.config["leviton_email"]
            password = self.config["leviton_password"]
            self.leviton_session = DecoraWiFiSession()
            self.leviton_session.login(email, password)
            #print("Leviton account connected successfully.")
            self.load_leviton_devices()
        except Exception as e:
            print(f"Leviton error: {e}")
            #self.speak("Leviton connection failed. Check your credentials.")

    def load_leviton_devices(self):
        try:
            perms = self.leviton_session.user.get_residential_permissions()
            for perm in perms:
                acct = ResidentialAccount(self.leviton_session, perm.residentialAccountId)
                residences = acct.get_residences()
                for residence in residences:
                    switches = residence.get_iot_switches()
                    for switch in switches:
                        clean_name = switch.name.strip().lower()
                        self.leviton_switches[clean_name] = switch
            count = len(self.leviton_switches)
            #print(f"Found {count} Leviton light{'s' if count != 1 else ''}.")
            self.levitonlogin_ok = True
            #self.speak(f"Leviton account connected successfully. Found {count} Leviton light{'s' if count != 1 else ''}.")
        except Exception as e:
            print(f"Error loading devices: {e}")

    def handle_leviton_command(self, query):
        if not self.leviton_session:
            return False

        q = query.lower()

        # List lights
        if any(phrase in q for phrase in ["list my lights", "what lights", "show lights"]):
            if self.leviton_switches:
                names = ", ".join([s.name for s in self.leviton_switches.values()])
                print(names)
                self.speak(f"Your lights are: {names}")
            else:
                self.speak("No Leviton lights found.")
            return True

        # Normalize query: remove plurals and convert numbers to digits
        q_norm = q.replace("switches", "switch")  # singularize
        for word, digit in [("one", "1"), ("two", "2"), ("three", "3"),
                            ("four", "4"), ("five", "5"), ("six", "6"),
                            ("seven", "7"), ("eight", "8"), ("nine", "9")]:
            q_norm = q_norm.replace(word, digit)

        targets = []
        target_names = []

        # Check for "all"
        if any(word in q_norm for word in ["all", "every", "whole house"]):
            targets = list(self.leviton_switches.values())
            target_names = ["all lights"]
        else:
            # Try to match device names
            for name, switch in self.leviton_switches.items():
                # Normalize the switch name too
                name_norm = name.lower().replace(" ", "")
                q_check = q_norm.replace(" ", "")
                if name_norm in q_check or q_check in name_norm:
                    targets.append(switch)
                    target_names.append(switch.name)

        if not targets:
            return False

        target_name = ", ".join(target_names)

        # Turn on/off
        if any(phrase in q for phrase in ["turn on", "on", "activate"]):
            for t in targets:
                t.update_attributes({"power": "ON"})
            #self.speak(f"Turning on {target_name}")
            return True

        if any(phrase in q for phrase in ["turn off", "off", "deactivate"]):
            for t in targets:
                t.update_attributes({"power": "OFF"})
            #self.speak(f"Turning off {target_name}")
            return True

        return False

        # Brightness
        match = re.search(r'(?:to|at)\s*(\d+)\s*%?', q)
        if match:
            level = int(match.group(1))
            level = max(1, min(100, level))  # clamp 1-100
            for t in targets:
                t.update_attributes({"power": "ON", "brightness": level if level > 0 else "OFF"})
            action = "off" if level == 0 else f"to {level} percent"
            self.speak(f"Setting {target_name} {action}")
            return True

        return False

    def login_ring_official_async(self):
        cache_file = Path("ring_token.cache")
        def token_updated(token):
            cache_file.write_text(json.dumps(token))
            print("Ring token saved!")
        def otp_callback():
            code = input("Enter Ring 2FA code: ").strip()
            return code

        async def do_ring_auth():
            user_agent = "AI/1.0"
            auth = Auth(user_agent, None, token_updated)
            try:
                await auth.async_fetch_token(self.config["ring_email"], self.config["ring_password"])
            except Requires2FAError:
                await auth.async_fetch_token(self.config["ring_email"], self.config["ring_password"], otp_callback())
            ring = Ring(auth)
            await ring.async_update_devices()
            return ring

        try:
            #print("\n=== RING LOGIN ===")
            if cache_file.is_file():
                auth = Auth("AI/1.0", json.loads(cache_file.read_text()), token_updated)
                self.ring = Ring(auth)
                self.ring.update_devices()
                #print("Loaded saved Ring token.")
            else:
                print("No saved token. Logging in...")
                self.ring = asyncio.run(do_ring_auth())

            # FINAL — WORKS PERFECTLY WITH YOUR ACCOUNT
            raw_devices = self.ring.devices()
            self.ring_devices = []

            # raw_devices is dict-like but not a real dict → use key access
            for key in raw_devices:
                dev_list = raw_devices[key]
                if isinstance(dev_list, list):
                    self.ring_devices.extend(dev_list)

            # Remove duplicates and fix any missing names
            seen = set()
            for d in self.ring_devices[:]:
                if d.id in seen:
                    self.ring_devices.remove(d)
                    continue
                seen.add(d.id)
                if not hasattr(d, 'name') or d.name is None:
                    d.name = f"Camera {d.id[-4:]}"

            count = len(self.ring_devices)
            if count > 0:
                names = ", ".join(d.name for d in self.ring_devices)
                #print(f"Ring connected. {count} cameras online.")
                #print(f"Ring devices: {names}")
                print(f"Ring devices: {self.ring_devices}")
                self.ringlogin_ok = True
            #else:
                #self.speak("Ring connected but no cameras detected.")

        except Exception as e:
            print(f"Ring failed: {e}")
            self.speak("Ring login failed.")

    def listen(self):
        with self.mic as source:
            print("Listening...")
            self.r.adjust_for_ambient_noise(source, duration=3)  # longer calibration
            self.r.energy_threshold = 100                     # lower = more sensitive
            self.r.dynamic_energy_threshold = True            # auto-adjust threshold

            try:
                audio = self.r.listen(source, timeout=10, phrase_time_limit=20)
            except sr.WaitTimeoutError:
                print("Listening timed out.")
                return ""

        try:
            results = self.r.recognize_google(audio, language="en-US", show_all=True)
            if results:
                # Google returns alternatives; pick the most confident
                if isinstance(results, dict) and "alternative" in results:
                    best_guess = results["alternative"][0]["transcript"]
                else:
                    best_guess = str(results)
            else:
                best_guess = ""

            print(f"You: {best_guess}")
            return best_guess.lower()
        except sr.UnknownValueError:
            print("Could not understand audio")
            return ""
        except Exception as e:
            print(f"Speech recognition error: {e}")
            return ""


    def handle_ring(self, q):
        if not self.ring_devices: return False
        q = q.lower()
        keywords = {"live", "stream", "show"}
        device = next((d for d in self.ring_devices if d.name.lower() in q), None)
        if not device:
            self.speak("No Ring device found.")
            return True
        if any(kw in q.lower() for kw in keywords):
            self.speak(f"Opening live view from {device.name}")
            try:
                # Fallback to Ring web URL if video_url() fails
                url = f"https://account.ring.com/account/dashboard/?lv_d={device.id}"
                webbrowser.open(url)
            except:
                self.speak("Live view unavailable. You need a ring subscription to access this.")
            return True
        if "snapshot" in q:
            try:
                url = device.snapshot_url(int(time.time()))
                fn = f"{device.name.replace(' ', '_')}.jpg"
                urllib.request.urlretrieve(url, fn)
                os.startfile(fn)
                self.speak("Snapshot taken.")
            except:
                self.speak("Snapshot failed.")
            return True
        return False
    
    def web_fallback_answer(self, query):
        try:
            print(f"🔍 Searching for: {query}")

            # ===== 0. Try to evaluate math expressions first =====
            math_expr = re.findall(r'[\d\.\+\-\*\/\^\(\) ]+', query)
            if math_expr:
                try:
                    expr = "".join(math_expr)
                    result = eval(expr)
                    print("Answer (Math):", result)
                    self.speak(f"The answer is {result}")
                    return True
                except:
                    pass  # Not a valid math expression, continue

            # ===== 1. DuckDuckGo Instant Answer API =====
            ddg_url = f"https://api.duckduckgo.com/?q={urllib.parse.quote(query)}&format=json&no_html=1&skip_disambig=1"
            r = requests.get(ddg_url, timeout=5)
            data = r.json()

            if data.get("AbstractText"):
                answer = data["AbstractText"]
                print("Answer (DDG API):", answer)
                self.speak(answer)
                return True

            # Try related topics
            for topic in data.get("RelatedTopics", []):
                if isinstance(topic, dict) and topic.get("Text"):
                    answer = topic["Text"]
                    print("Answer (DDG topic):", answer)
                    self.speak(answer)
                    return True

            # ===== 2. Wikipedia API =====
            wiki_url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{urllib.parse.quote(query)}"
            r = requests.get(wiki_url, timeout=5)
            if r.status_code == 200:
                data = r.json()
                if "extract" in data and len(data["extract"]) > 50:
                    answer = data["extract"]
                    print("Answer (Wikipedia):", answer)
                    self.speak(answer)
                    return True

            # ===== 3. Google search fallback =====
            print("No API answer found, trying Google HTML search...")
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
            }
            google_url = f"https://www.google.com/search?q={urllib.parse.quote_plus(query)}"
            r = requests.get(google_url, headers=headers, timeout=5)
            soup = BeautifulSoup(r.text, "html.parser")

            # Try featured snippet
            snippet = soup.select_one("div[data-attrid='wa:/description'], div[data-tts='answers'], div.BNeawe.iBp4i.AP7Wnd")
            if snippet:
                answer = snippet.get_text()
                print("Answer (Google snippet):", answer)
                self.speak(answer)
                return True

            # Final fallback
            self.speak("I couldn't find a direct answer, but you can check online.")
            return True

        except Exception as e:
            print("Web lookup failed:", e)
            self.speak("I couldn't reach the internet.")
            return False
    
    def loaded(self):
        #print("Starting......\n")
        print(f"Welcome back, {self.user_name}.")
        self.speak(f"Welcome back, {self.user_name}.")

        # Check Leviton status
        if self.levitonlogin_ok:
            count = len(self.leviton_switches)
            print(f"Leviton: ✓ Connected — {count} lights ready")
            #self.speak(f"Leviton connected. {count} lights ready.")
        else:
            print("Leviton: ✗ Failed or unavailable")
            self.speak("Leviton unavailable.")

        # Check Ring status
        if self.ringlogin_ok:
            count = len(self.ring_devices)
            names = ", ".join([d.name for d in self.ring_devices[:3]])  # first 3 names
            if count > 3:
                names += " and more"
            print(f"Ring: ✓ Connected — {count} cameras ready")
            #self.speak(f"Ring connected. {count} cameras online.")
        else:
            print("Ring: ✗ Failed or unavailable")
            self.speak("Ring unavailable.")

        # Final status
        if self.levitonlogin_ok and self.ringlogin_ok:
            print("All systems operational!")
            self.speak("All systems operational.")
        else:
            print("Partial systems online.")
            self.speak("Systems partially operational.")
        
    def respond(self, q):
        q = q.lower()
        # === LEVITON LIGHTS ===
        if self.handle_leviton_command(q):
            return
        
        # === RING CAMERAS ===
        keywords = {"live", "stream", "show"}
        if any(kw in q.lower() for kw in keywords):
            if self.handle_ring(q):
                return     
            
        if "how are you" in q:
            print("Fully operational, Sir.")
            self.speak("Fully operational, Sir.")
        elif "time" in q:
            t = datetime.datetime.now().strftime("%I:%M %p")
            print(f"It is {t}.")
            self.speak(f"It is {t}.")
        elif "good night" in q:
            self.speak("Good night, Sir.")
            exit()
        else:
            self.web_fallback_answer(q)

    def run(self):
        while True:
            text = self.listen()
            if "jarvis" in text.lower():
                cmd = text.lower().replace("jarvis", "").strip()
                if cmd:
                    self.respond(cmd)
                else:
                    self.speak("Yes, Sir?")

if __name__ == "__main__":
    print("Starting......")
    AI().run()
