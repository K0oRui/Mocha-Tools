# Building Mocha Tools Android APK on Windows
# =============================================
# Everything runs inside WSL2 (Ubuntu). You do NOT need Android Studio.

## Step 1 — Install WSL2 (run in PowerShell as Administrator)

    wsl --install

Reboot when prompted. Open "Ubuntu" from the Start menu and set a username/password.

---

## Step 2 — Install system dependencies (inside WSL2)

    sudo apt update && sudo apt upgrade -y

    sudo apt install -y \
        python3-pip python3-venv git zip unzip \
        build-essential libffi-dev libssl-dev \
        zlib1g-dev libbz2-dev libreadline-dev \
        libsqlite3-dev libncurses5-dev libncursesw5-dev \
        liblzma-dev tk-dev uuid-dev libgdbm-dev \
        autoconf automake libtool pkg-config \
        cmake libltdl-dev openjdk-17-jdk \
        ccache

---

## Step 3 — Install Buildozer and Kivy dependencies

    pip3 install --user --upgrade pip
    pip3 install --user buildozer kivy kivymd

    # Make sure ~/.local/bin is on your PATH
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
    source ~/.bashrc

---

## Step 4 — Copy the android/ folder into WSL2

From Windows Explorer, your WSL2 Ubuntu files live at:

    \\wsl.localhost\Ubuntu\home\<your-username>\

Copy the android/ folder there, or clone/copy your repo.
The android/ folder should contain:
  - main.py
  - buildozer.spec

---

## Step 5 — First build (this downloads the Android SDK/NDK — takes ~30 min)

    cd ~/android       # or wherever you put the folder
    buildozer android debug

On the very first run, Buildozer downloads:
  - Android SDK (~1 GB)
  - Android NDK (~500 MB)
  - Python-for-Android recipes

Subsequent builds are much faster (under 5 minutes) because everything is cached.

---

## Step 6 — Get the APK

After a successful build, the APK is at:

    android/bin/mochatools-1.0.0-arm64-v8a_armeabi-v7a-debug.apk

Copy it to Windows via Explorer (\\wsl.localhost\Ubuntu\...) or:

    cp bin/mochatools-*.apk /mnt/c/Users/<YourWindowsUsername>/Desktop/

---

## Step 7 — Install on your Android device

Enable "Install from unknown sources" on your device, then either:
  - Transfer the APK via USB and tap to install
  - Use `adb install bin/mochatools-*.apk` if you have ADB set up

---

## Release build (no "debug" watermark)

    buildozer android release

You'll need a keystore to sign it. Generate one:

    keytool -genkey -v -keystore mocha.keystore \
        -alias mocha -keyalg RSA -keysize 2048 -validity 10000

Then sign:

    jarsigner -verbose -sigalg SHA256withRSA -digestalg SHA-256 \
        -keystore mocha.keystore \
        bin/mochatools-*-release-unsigned.apk mocha

    zipalign -v 4 \
        bin/mochatools-*-release-unsigned.apk \
        bin/mochatools-release.apk

---

## Common errors

**aidl not found / SDK license error**
    buildozer android debug -- --accept-sdk-licenses

**Gradle download fails behind a proxy**
Add to buildozer.spec:
    android.gradle_dependencies =
And set HTTP_PROXY / HTTPS_PROXY env vars in WSL2.

**"No module named kivymd"**
Make sure requirements in buildozer.spec includes `kivymd==1.2.0`

**File picker does nothing on device**
plyer requires READ_EXTERNAL_STORAGE permission — already set in buildozer.spec.
On Android 13+, the app requests READ_MEDIA_* permissions on first launch automatically.

**App crashes on launch — check logs**
    adb logcat | grep -i "python\|kivy\|mocha"