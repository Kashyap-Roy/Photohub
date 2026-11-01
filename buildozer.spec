[app]

# (str) Title of your application
title = PhotoHub

# (str) Package name
package.name = photohub

# (str) Package domain (needed for android packaging)
package.domain = org.photohub

# (str) Source code where the main.py lives
source.dir = .

# (list) Source files to include (let empty to include all the files)
source.include_exts = py,png,jpg,jpeg,webp,kv,atlas,json

# (list) List of inclusions using pattern matching
source.include_patterns = assets/*,static/*

# (str) Application versioning
version = 1.0.0

# (list) Application requirements
# FIXED: Let python3 dynamically match the host runner version
requirements = python3==3.11.5,hostpython3==3.11.5,kivy==2.3.0,pyjnius,android

# add these two under [app]:
p4a.branch = v2024.01.21

# change the NDK and API to versions that release is tested with:
android.api = 33
android.minapi = 24
android.sdk = 33
android.ndk = 25b

# (str) Supported orientation (one of landscape, sensorLandscape, portrait or all)
orientation = portrait

# (bool) Indicate if the application should be fullscreen
fullscreen = 0

#
# Android specific
#

# (list) Permissions
android.permissions = CAMERA,READ_EXTERNAL_STORAGE,WRITE_EXTERNAL_STORAGE,READ_MEDIA_IMAGES,INTERNET,ACCESS_NETWORK_STATE

# (list) Android application features (declares front camera capability)
android.features = android.hardware.camera,android.hardware.camera.front

# (int) Target Android API (Android 14)
android.api = 34

# (int) Minimum API your APK will support (Android 7.0+)
android.minapi = 24

# (int) Android SDK version to use
android.sdk = 34

# (str) Android NDK version to use
android.ndk = 26b

# (bool) If True, then skip trying to update the Android sdk
android.skip_update = False

# (bool) If True, then automatically accept the SDK license
android.accept_sdk_license = True

# (str) The Android arch to build for (64-bit ARM for modern Android devices)
android.archs = arm64-v8a

# (bool) enables Android auto backup feature (Android API >=23)
android.allow_backup = True

[buildozer]

# (int) Log level (0 = error only, 1 = info, 2 = debug (with command output))
log_level = 2

# (int) Display warning if buildozer is run as root (0 = False, 1 = True)
warn_on_root = 0
