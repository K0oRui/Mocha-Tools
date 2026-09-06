<img src=".github/resources/banner.png" alt="Mocha Tools banner" width="900">

![Status](https://img.shields.io/badge/STATUS-ACTIVE-D0B276?style=for-the-badge&labelColor=000000&logo=github&logoColor=C8A96E)
![Python](https://img.shields.io/badge/Python-3.11-D0B276?style=for-the-badge&labelColor=000000&logo=python&logoColor=C8A96E)
![License](https://img.shields.io/github/license/nxllvxxd/Mocha-Tools?style=for-the-badge&color=D0B276&labelColor=000000)

![Commits](https://img.shields.io/github/commit-activity/m/nxllvxxd/Mocha-Tools?style=for-the-badge&color=D0B276&labelColor=000000&label=Commits+This+Month)
![Last Commit](https://img.shields.io/github/last-commit/nxllvxxd/Mocha-Tools?style=for-the-badge&color=D0B276&labelColor=000000&logo=github)
![Repo Size](https://img.shields.io/github/repo-size/nxllvxxd/Mocha-Tools?style=for-the-badge&color=D0B276&labelColor=000000)

[![Typing SVG](https://readme-typing-svg.demolab.com?font=Fira+Code&pause=1000&color=D0B276&background=000000&width=900&lines=Cross+platform+tools+for+Mocha+written+in+Python;Designed+to+be+compiled+with+Nuitka)](https://git.io/typing-svg)
<p align="center">
  <img src=".github/resources/screenshot.png" alt="Mocha Tools main window" width="720">
</p>

![Divider](https://capsule-render.vercel.app/api?type=rect&color=D0B276&height=3)

## Features
- Upload files to Mocha with drag and drop, or by picking them in the file manager.
- Upload whole folders.
- Upload speed and progress indicators.
- Create share links with every option available in the Mocha API.
- Browse files and folders.
- Togglable debug mode for easier troubleshooting.
- Manage shares: view them, toggle active or inactive, delete them.
- Remote ingest support.
- Upload multiple files and folders at once with mass upload.
- Preview files in your storage, from text to video and audio:
    - Video: .mp4, .mov, .avi, .mkv, .wmv, .flv, .webm, .m4v, .mpeg, .mpg, .3gp
    - Audio: .mp3, .flac, .m4a, .wav, .ogg, .aac, .wma, .opus, .aiff, .aif
    - Images: .jpg, .jpeg, .png, .gif, .bmp, .webp, .ico, .tiff, .tif, .svg
    - Text: .txt, .md, .markdown, .rst, .log, .csv, .tsv, .py, .pyw, .js, .mjs, .cjs, .ts, .tsx, .jsx, .html, .htm, .css, .scss, .sass, .less, .json, .jsonc, .xml, .yaml, .yml, .toml, .ini, .cfg, .c, .h, .cpp, .hpp, .cc, .cs, .java, .kt, .swift, .go, .rs, .rb, .php, .lua, .sh, .bash, .zsh, .ps1, .sql, .env, .gitignore, .gitattributes, .dockerfile, .vue, .svelte, .graphql, .proto, .bat, .r, .pl
- Sync folders between Mocha and your local PC. Watch a folder and let Tools handle the uploads.
- Toggle the tray icon in settings to keep Tools open without taking up taskbar space.

![Divider](https://capsule-render.vercel.app/api?type=rect&color=D0B276&height=3)

## Credits
Mocha, the API, and access to both come from [Bink-lab](https://github.com/Bink-lab). Thank you for the contributions too.

## Source Requirements
- Python 3.11 **ONLY** (can be downloaded [here](https://www.python.org/downloads/))
- PySide6
- requests
- nuitka
- packaging
- keyring
- mutagen
- ffmpeg-python
- A Mocha account and an API key, which can be obtained [here](https://mocha.my)

![Divider](https://capsule-render.vercel.app/api?type=rect&color=D0B276&height=3)

### Running From Source
1. `git clone https://github.com/nxllvxxd/Mocha-Tools`
2. `cd Mocha-Tools/apps/desktop`
3. `pip install -r requirements.txt`
4. `py -3.11 mochatools.py`

![Divider](https://capsule-render.vercel.app/api?type=rect&color=D0B276&height=3)

## Preview

<p align="center">
  <img src=".github/resources/massupload.gif" alt="Mass uploading files in Mocha Tools" width="420">
</p>
<p align="center">
  <img src=".github/resources/remoteingest.gif" alt="Uploading files with drag and drop" width="400">
  <img src=".github/resources/sharetab.gif" alt="Starting a remote ingest job" width="400">
</p>
<p align="center">
  <img src=".github/resources/dragdrop.gif" alt="Creating a Mocha share link" width="848">
</p>

![Divider](https://capsule-render.vercel.app/api?type=rect&color=D0B276&height=3)

## Roadmap
| Idea | Complete? |
| :---- | :----: |
| Merge mass upload and upload | ✅ |
| Custom colors support | ✅ |
| Custom font support | ✅ |
| Folder sync support | ✅ |
| File previews (images, video, audio) | ✅ |
| Download support for your own files | ✅ |
| Android version | ❌ |
| Context menu integration for easy uploading | ❌ |
| Complete control over files: deletion, moving, sharing | ✅ |
| Debug and token management in its own tab | ✅ |
| Multiple files and folders at once | ✅ |
| Configurable upload settings, such as chunk size and thread count | ✅ |

![Divider](https://capsule-render.vercel.app/api?type=rect&color=D0B276&height=3)

## Known Issues
|**ISSUES**|
| :---- |
|<ul><li>~~Folder rename not functioning~~</li><li>Updating is broken on Mac</li><li>~~MacOS build seems to not be functioning according to reports~~</li><li>~~Need to make the options tab appear more consistent~~</li><li>~~Deleting multiple shares does not work~~</li><li>~~Progress bar glitches after canceling upload~~</li><li>~~Under 50mb files are kinda buggy and drop resulting in EOF issues~~</li><li>~~100GB files not functioning (might be misreport will look into)~~ (seems to be fixed unsure)</li><li>~~Selecting move folder doesn't select folder if inside~~</li><li>~~Upload speed and percent is buggy (especially on large files)~~</li><li>~~Unable to toggle share as active or inactive~~</li><li>~~Share link creation creates share but provides incorrect link~~</li><li>~~Folder upload just dumps all files in root without creating new folder~~</li><li>~~Original file names not being listed~~ Thank you [Bink-lab](https://github.com/Bink-lab)</li><li>~~Unable to move files~~</li><li>~~Unable to ~~create~~ or view shares~~</li><li>~~Large file upload is not working correctly~~ Thank you [Bink-lab](https://github.com/Bink-lab)</li><li>~~Uploading to specific existing folders is not functioning~~</li><li>~~Moving files or folders deeper than one folder does not function~~</li><li>~~Uploading deeper than one folder is not working~~</li></ul>|