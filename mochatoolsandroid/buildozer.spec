[app]

# App identity
title = Mocha Tools
package.name = mochatools
package.domain = com.nxllxvxxd2

# Entry point
source.dir = .
source.include_exts = py,png,jpg,kv,atlas,json
source.main = main.py

# Version — keep in sync with your VERSION file
version = 1.0.0

# Requirements
# requests is pure Python so it works fine
# plyer gives us the file picker on Android
requirements = python3,kivy,kivymd,requests,urllib3,certifi,charset-normalizer,idna,plyer,pyjnius,packaging,android
# Orientation
orientation = portrait

# Android specifics
android.minapi    = 30
android.api       = 34
android.ndk       = 25b
android.sdk       = 34
android.archs     = arm64-v8a, armeabi-v7a

# Permissions needed for file access and network
android.permissions = INTERNET,READ_EXTERNAL_STORAGE,WRITE_EXTERNAL_STORAGE,READ_MEDIA_IMAGES,READ_MEDIA_VIDEO,READ_MEDIA_AUDIO,MANAGE_EXTERNAL_STORAGE

# Use the storage permission model for Android 10+
android.allow_backup = True

# Icons — put your icon at the paths below or swap these out
icon.filename = %(source.dir)s/presplash.png
presplash.filename = %(source.dir)s/presplash.png

# Fullscreen (0 = show status bar, which is nicer on Android)
fullscreen = 0

# Black background during load
android.presplash_color = #111010

# gradle extras — needed for KivyMD
# android.gradle_dependencies =

# p4a branch — use buildozer's default stable release
# p4a.branch = master

[buildozer]

# Log level: 0=error, 1=info, 2=debug
log_level = 2
# Warn only on unstable (not error)
warn_on_root = 1