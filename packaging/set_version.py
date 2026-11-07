"""Met le numéro de version de l'étiquette publiée (v1.2.3 → 1.2.3) dans le code, avant la construction : il n'y a
pas de numéro à changer à la main. Usage : python packaging/set_version.py v1.2.3"""

import re
import sys
from pathlib import Path

version = sys.argv[1].removeprefix("v")
if not re.fullmatch(r"\d+\.\d+\.\d+", version):
    sys.exit(f"Étiquette de version invalide : {sys.argv[1]} (attendu : v1.2.3)")
root = Path(__file__).resolve().parent.parent
for path, pattern in ((root / "src/biblio/__init__.py", r'__version__ = "[^"]*"'), (root / "pyproject.toml", r'^version = "[^"]*"')):
    text = path.read_text(encoding="utf-8")
    new = re.sub(pattern, pattern.split(" = ")[0].lstrip("^") + f' = "{version}"', text, count=1, flags=re.M)
    if new == text and f'"{version}"' not in text:
        sys.exit(f"Numéro de version introuvable dans {path.name}")
    path.write_text(new, encoding="utf-8")
print(f"Version {version}")
