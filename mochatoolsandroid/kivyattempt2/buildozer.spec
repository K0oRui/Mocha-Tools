[app]

title = Mocha Tools
package.name = mochatools
package.domain = org.nxllxvxxd2

source.dir = .
source.include_exts = py,png,jpg,kv,atlas

version = 3.0.5

requirements = python3,kivy==2.3.1,kivymd==1.2.0,requests,urllib3,certifi,charset-normalizer,idna,plyer,pyjnius,packaging,materialyoucolor

orientation = portrait

fullscreen = 0

icon.filename = %(source.dir)s/data/icon.png

presplash.filename = %(source.dir)s/data/presplash.png

android.permissions = INTERNET,READ_EXTERNAL_STORAGE,WRITE_EXTERNAL_STORAGE,ACCESS_FINE_LOCATION,ACCESS_COARSE_LOCATION

android.api = 33

android.minapi = 21

android.ndk = 25b

android.accept_sdk_license = True

android.archs = arm64-v8a,armeabi-v7a

[buildozer]

log_level = 2

warn_on_root = 1
