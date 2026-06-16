import sys

# Verify runtime Python version early so users on systems with multiple
# interpreters get a clear message. Require Python 3.11.x (major=3, minor=11).
ver = sys.version_info
print(f"Python runtime: {ver.major}.{ver.minor}.{ver.micro} (sys.version={sys.version})")
if not (ver.major == 3 and ver.minor == 11):
    # Allow an explicit override when the user knows what they're doing
    if not ("MOCHA_ALLOW_OLD_PY" in sys.environ):
        print("ERROR: Mocha Tools requires Python 3.11.x. Start with a 3.11 interpreter or set MOCHA_ALLOW_OLD_PY=1 to override.")
        sys.exit(1)

from mochatools_app.app import main


if __name__ == "__main__":
    main()
