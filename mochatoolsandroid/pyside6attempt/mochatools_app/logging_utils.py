def write_debug_log(msg):
    try:
        with open("mochatools.log", "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass
