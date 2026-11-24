import os


def load_env():
    """Repo kokundeki veya bot1haber klasorundeki .env dosyasini yukler."""
    src_dir = os.path.dirname(os.path.abspath(__file__))
    bot_dir = os.path.dirname(src_dir)
    repo_dir = os.path.dirname(bot_dir)

    for base in (bot_dir, repo_dir):
        env_path = os.path.join(base, ".env")
        if os.path.isfile(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                for raw_line in f:
                    line = raw_line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip().strip('"').strip("'")
                    if key:
                        os.environ.setdefault(key, val)
