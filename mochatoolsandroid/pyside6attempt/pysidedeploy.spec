[app]
title = MochaTools
project_dir = .
input_file = main.py
project_file =
exec_directory =

[python]
# setup_and_build.sh rewrites this to the active Ubuntu venv path.
python_path = ~/mochatools-android-env/bin/python3
android_packages = buildozer,cpython
packages = requests, certifi, urllib3, charset-normalizer, idna, packaging
protected_packages = requests, certifi, urllib3, charset-normalizer, idna, packaging

[qt]
modules = Core, Gui, Widgets
plugins =

[android]
# setup_and_build.sh downloads/fills these with qtpip for aarch64/arm64-v8a.
wheel_pyside = ~/mochatools-android-wheels/PySide6-android-aarch64.whl
wheel_shiboken = ~/mochatools-android-wheels/shiboken6-android-aarch64.whl
plugins = platforms, imageformats

[buildozer]
mode = debug
recipe_dir =
jars_dir =
ndk_path = ~/Android/Sdk/ndk/26.1.10909125
sdk_path = ~/Android/Sdk
local_libs =
arch = aarch64
