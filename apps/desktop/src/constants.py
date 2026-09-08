CHUNK_SIZE = 50 * 1024 * 1024
PART_UPLOAD_RETRIES = 10
PART_UPLOAD_TIMEOUT = 7200
S3_DEFAULT_CONCURRENCY = 24
S3_MAX_CONCURRENCY = 24
RELAY_DEFAULT_CONCURRENCY = 1
RELAY_MAX_CONCURRENCY = 1

DEFAULT_CHUNK_SIZE_MB = 50
DEFAULT_MAX_CHUNKS = 20
APP_NAME = "MochaTools"
ORG_NAME = "Mocha"
HARDCODED_BASE_URL = "https://api.mocha.my"
SHARE_BASE_URL = "https://mocha.my"

# Stamped at build time by build.py — do not edit manually.
APP_VERSION = "8.0.0"
APP_CHANGES: list[dict[str, str]] = [
    {
        "subject": "build: custom PySide6 installer and release pipeline",
        "description": "- replace Inno/NSIS with a custom PySide6 installer\n- cross-platform Nuitka builds for Windows, Linux, and macOS\n- changelog generation with a random cat gif\n- automatic publishing with workflow and cache cleanup",
    },
    {
        "subject": "feat(desktop): native frameless window chrome with aero snap",
        "description": "- Frameless window now supports native Aero Snap and edge resize via WS_THICKFRAME, with WM_NCCALCSIZE / WM_NCHITTEST / WM_GETMINMAXINFO handled through Qt's native message filter.\n\n- Adaptive corner rounding: corners are rounded by default and squared off when the window is snapped, maximised, or flush against another visible window. Adjacency is detected event-driven via SetWinEventHook (replacing the old polling timer) plus EnumWindows edge checks.\n\n- moveEvent / changeEvent re-evaluate rounding on move and window-state changes.\n\n- New coffee-cup brand icon (Phosphor-style SVG) rendered at multiple resolutions for the window, titlebar, and tray; WM_SETICON is sent so the taskbar shows the gold icon (Windows-only, guarded for other platforms).\n\n- Titlebar icon is clickable (opens https://mocha.my) and sized at 20px.\n\n- Updated icon assets for Windows (.ico), macOS (.icns), and Debian/Ubuntu (.png); build script updated.",
    },
    {
        "subject": "fix(upload): accurate ETA/speed, faster folder creation, restart cancelled",
        "description": "- Share one progress tracker per job so multi-file uploads keep cumulative progress and a stable speed window instead of resetting per file\n- Sum concurrent mass-upload speeds and decay stalled speeds toward zero so ETA/speed stay accurate during stalls\n- Show mass uploads in tray ETA (activity derived from queue)\n- Create each destination folder once and in parallel instead of re-walking the full ancestor chain per folder\n- Allow restarting cancelled mass uploads via Start upload\n- Poll only active tabs and slow interval to 15s",
    },
    {
        "subject": "fix(api): align client with documented Mocha API contract",
        "description": "- list_files follows cursor/hasMore pagination and merges pages\n- multipart_init sends directPartSizeBytes; chunk by the server's\n  directPartSizeBytes and key the mode off directPartUpload instead\n  of the absent strategy field\n- drop undocumented sourcePath from move; require fileId/folderPath\n- create_share supports folderPath, password, and expiresInHours: null\n- get_share accepts accessToken; presigned_url accepts disposition\n- trim multipart_presign and relay part payloads to documented fields",
    },
    {
        "subject": "chore(lint): enforce ruff, pyright, and CI lint gate across desktop app",
        "description": "- Enable PTH pathlib rules (97 findings) and fix 3 autofix-introduced\n  str/Path bugs in sync_tab, widgets, and updater\n- Enable PLR2004 magic-value and PLW2901 loop-rebinding rules; add\n  named constants for KB sizes, HTTP codes, tab indices, and thresholds\n- Enable complexity rules (PLR0911/0912/0913/0915/0917) with raised\n  pylint thresholds matching current worst-case functions\n- Add type annotations, contextlib.suppress, and sha256 listing hashes\n- Add pyproject.toml ruff config, pyrightconfig.json, and lint.yml CI\n  workflow (ruff check, ruff format, pyright)",
    },
    {
        "subject": "refactor(app): replace lambda closures with dependency injection",
        "description": "Replace signal-wiring lambdas with bound methods and functools.partial,\nconstructor-injected lambdas with typed provider objects (providers.py),\nand ctx.upload_job_id signal filtering with per-job UploadManager\nsubscribe/unsubscribe routing. Fixes startup AttributeError from eager\nwidget access in the API key provider.",
    },
    {
        "subject": "refactor(upload): unify single, mass, and sync uploads on shared pipeline",
        "description": "Add UploadManager/UploadJob queue with a global concurrency cap and\nper-source priority (sync yields to single/mass). Route all three upload\npaths through it via job-ID + ref signals. Replace the per-tab concurrency\nspins with a single global setting and drop the dead ctx.upload_worker.",
    },
    {
        "subject": "refactor: extract shared MochaClient and wire it through the app",
        "description": "Move all HTTP into a Qt-free MochaClient (single requests.Session, lazy\nAPI key, unified MochaAPIError) and make workers/dialogs thin wrappers\nthat take the shared client instead of api_key/base_url. One client is\ncreated in AppContext and passed to every tab, worker, and the poller.\n\nFixes:\n- presign part uploads: _presign_part_url treated the client's returned\n  URL string as a dict, so every S3 part failed with 'No presigned URL\n  in response'\n- app crash when the system tray is unavailable (no-op tray fallbacks)\n\nDebug logging is now more informative: per-request ids, elapsed time,\nresponse size/body, truncated S3 PUT urls with part numbers, and\ntimestamps in mochatools.log.",
    },
    {
        "subject": "refactor(app): decompose god object into focused modules with AppContext dataclass",
        "description": "Split 1,794-line MochaTools class into 7 focused modules:\n- utils.py: pure helper functions (parse_release_notes_md, fmt_bytes, etc.)\n- settings.py: merged settings tab + settings_sections into single module\n- window_chrome.py: frameless window resize/rounding logic\n- tray_manager.py: system tray setup, tooltip timer, upload status\n- update_controller.py: check/install updates, release info dialogs\n- upload_manager.py: upload tab UI, upload flow, signals, badge logic\n- entrypoint.py: main(), app palette, theme signal wiring\n\nSlimmed app.py to ~370-line orchestrator with AppContext dataclass\nholding all shared cross-module state (upload, update, tray, workers).\n\nUpdated tray_manager, upload_manager, update_controller to accept\n(win, ctx) parameters instead of using win._xxx private attributes.\n\nFixed settings.py relative imports (.. to .) since it's at package level.\nFixed entrypoint.py to import main from correct module.\n\nAll ruff checks passing. App verified launching successfully.",
    },
    {
        "subject": "refactor: restructure repo into monorepo layout",
        "description": "Move all app code, build assets, and installer scripts under\napps/desktop/ to prepare for a monorepo structure with room\nfor additional apps (e.g. web, cli).\n\n- mochatools.py, mochatools_app/, requirements.txt → apps/desktop/\n- builditems/ → apps/desktop/build/\n- installer.nsi, installer.sh → apps/desktop/\n- scripts/build.bat → apps/desktop/build/build.bat\n- VERSION → apps/desktop/VERSION\n\nUpdate all path references in build.yml, stamp_version.py,\nbuild.bat, installer.nsi, and installer.sh.",
    },
    {
        "subject": "build: migrate packaging from PyInstaller to Nuitka and PyQt6 to PySide6",
        "description": "Replace PyInstaller with Nuitka for the onefile/app builds across the\nWindows, Linux, and macOS CI jobs and the new local scripts/build.bat.\n\n- requirements.txt: pyinstaller -> nuitka, PyQt6 -> PySide6\n- build.yml: rewrite all four build jobs to use python -m nuitka\n  with --jobs, --disable-cache=dll-dependencies, and the pyside6 plugin\n- updater.py: add _is_compiled() helper that detects both PyInstaller's\n  sys.frozen and Nuitka's __compiled__ builtin; use it in _current_exe()\n  and the three _test_mode sites\n- scripts/build.bat: new local build script using a minimal .venv,\n  version stamping, stale-artifact cleanup, and optional NSIS installer\n- Drop --include-qt-plugins=all so Nuitka uses its sensible plugin set,\n  shrinking the Windows exe from ~127 MB to ~26 MB\n- .gitignore: ignore the new .venv/\n- README: update source requirements\n- Migrate all PyQt6 imports and pyqtSignal to PySide6 equivalents",
    },
]

UPDATE_CHECK_URL = "https://api.github.com/repos/nxllvxxd/Mocha-Tools/releases/latest"
