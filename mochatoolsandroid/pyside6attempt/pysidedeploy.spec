[app]
title = MochaTools
project_dir = .
input_file = main.py
project_file =

[python]
# Set this to your venv python path, e.g.:
#   python_path = /home/youruser/mochatools-android-env/bin/python3
python_path =
packages = requests,certifi,urllib3,charset-normalizer,idna,packaging
protected_packages =

[qt]
modules = Core,Gui,Widgets
plugins = platforms/android,imageformats/qjpeg
android_deploy_abi = arm64-v8a

[android]
# Download android-specific wheels from:
#   https://download.qt.io/snapshots/ci/pyside/dev/latest/
# Look for: PySide6-*-android_arm64.whl and shiboken6-*-android_arm64.whl
wheel_pyside =
wheel_shiboken =
# Adjust ndk_path to your NDK version:
ndk_path = ~/Android/Sdk/ndk/26.1.10909125
sdk_path = ~/Android/Sdk
build_tools_version = 34.0.0
android_min_api = 24
android_target_api = 34
application_binary = org.mochatools.app
activity_name = .MochaToolsActivity
