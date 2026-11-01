[app]

title = PhotoHub
package.name = photohub
package.domain = org.photohub

source.dir = .
source.include_exts = py,png,jpg,jpeg,webp,kv,atlas,json
source.include_patterns = assets/*,static/*

version = 1.0.0

requirements = python3==3.11.5,hostpython3==3.11.5,kivy==2.3.0,pyjnius,android

orientation = portrait
fullscreen = 0

android.permissions = CAMERA,READ_EXTERNAL_STORAGE,WRITE_EXTERNAL_STORAGE,READ_MEDIA_IMAGES,INTERNET,ACCESS_NETWORK_STATE
android.features = android.hardware.camera,android.hardware.camera.front

android.api = 33
android.minapi = 24
android.sdk = 33
android.ndk = 25b
android.skip_update = False
android.accept_sdk_license = True
android.archs = arm64-v8a
android.allow_backup = True

p4a.branch = v2024.01.21


[buildozer]

log_level = 2
warn_on_root = 0
