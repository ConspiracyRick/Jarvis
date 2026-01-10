import speech_recognition as sr
import pyttsx3
import datetime
import sys
import json
import os
import time
import webbrowser
import urllib.request
import requests
from pathlib import Path
import asyncio  # For Ring async login
from queue import Queue
import threading
import re

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
                self.speak(f"Your lights are: {names}")
            else:
                self.speak("No Leviton lights found.")
            return True

        # Find target device(s)
        target_switch = None
        for name, switch in self.leviton_switches.items():
            if name in q:  # matches full name or substring
                target_switch = switch
                break

        # All lights if no specific name or "all"/"every"
        if not target_switch or any(word in q for word in ["all", "every", "whole house"]):
            targets = list(self.leviton_switches.values())
            target_name = "all lights"
        else:
            targets = [target_switch]
            target_name = target_switch.name

        # Turn on/off
        if any(phrase in q for phrase in ["turn on", "on ", "activate"]):
            for t in targets:
                t.update_attributes({"power": "ON"})
            self.speak(f"Turning on {target_name}")
            return True

        if any(phrase in q for phrase in ["turn off", "off ", "deactivate"]):
            for t in targets:
                t.update_attributes({"power": "OFF"})
            self.speak(f"Turning off {target_name}")
            return True

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
                self.ringlogin_ok = True
            #else:
                #self.speak("Ring connected but no cameras detected.")

        except Exception as e:
            print(f"Ring failed: {e}")
            self.speak("Ring login failed.")

    def listen(self):
        with self.mic as source:
            print("Listening...")  # ← visual feedback
            self.r.adjust_for_ambient_noise(source, duration=1)  # ← longer calibration
            self.r.energy_threshold = 600  # ← lower = more sensitive (tune if needed)
            try:
                audio = self.r.listen(source, phrase_time_limit=100, timeout=60)
            except sr.WaitTimeoutError:
                return ""
        
        try:
            q = self.r.recognize_google(audio, language="en-US")
            print(f"You: {q}")
            return q.lower()
        except sr.UnknownValueError:
            print("Could not understand audio")
            return ""
        except Exception as e:
            print(f"Speech recognition error: {e}")
            return ""

    def handle_ring(self, q):
        if not self.ring_devices: return False
        q = q.lower()
        device = next((d for d in self.ring_devices if d.name.lower() in q), None)
        if not device:
            self.speak("No Ring device found.")
            return True
        if "live" in q or "stream" in q:
            self.speak(f"Opening live view from {device.name}")
            try:
                # Fallback to Ring web URL if video_url() fails
                video_url = f"https://account.ring.com/account/dashboard/{device.id}"
                webbrowser.open(device.video_url())
            except:
                self.speak("Live view unavailable. You need a ing subscription to access this.")
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
            self.speak("All systems operational, Sir.")
        else:
            print("Partial systems online.")
            self.speak("Systems partially operational.")
        
    def respond(self, q):
        q = q.lower()
        # === LEVITON LIGHTS (THIS WAS MISSING!) ===
        if self.handle_leviton_command(q):
            return
        
        # === RING CAMERAS — THIS WAS MISSING! ===
        if self.handle_ring(q):
            return
        
        if "list cameras" in q or "show cameras" in q or "list ring" in q:
            if self.ring_devices:
                names = ", ".join([d.name for d in self.ring_devices])
                self.speak(f"Your cameras: {names}")
            else:
                self.speak("No cameras found.")
            return True        
            
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
            self.speak("Awaiting your command.")

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