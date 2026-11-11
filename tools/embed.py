#!/usr/bin/env python3
"""Bettet Skriptdateien als Compose-`configs` in docker-compose.yml ein.

Umbrel kopiert bei App-Updates nur docker-compose.yml, umbrel-app.yml, exports.sh,
*.template und hooks/. Alle anderen Dateien bleiben auf dem Stand der Erstinstallation.
Skripte, die mit Updates mitkommen sollen, liegen deshalb als `configs` mit `content:`
direkt in der Compose-Datei. Die Quelldatei im App-Ordner bleibt der Ort zum Bearbeiten
und Testen; danach `python tools/embed.py` ausführen (prüft mit --check nur).

Markierung in der Compose-Datei (wird komplett ersetzt):
    # BEGIN embedded <name> (<quelldatei>)
    ...
    # END embedded <name>
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARK = re.compile(r"^( *)# BEGIN embedded (\S+) \((\S+)\)\n.*?^ *# END embedded \2\n", re.S | re.M)


def render(indent, name, source, app_dir):
    text = (app_dir / source).read_text(encoding="utf-8").replace("$", "$$")  # Compose-Interpolation
    body = "".join(f"{indent}    {line}\n" if line.strip() else "\n" for line in text.splitlines())
    return (f"{indent}# BEGIN embedded {name} ({source})\n"
            f"{indent}# Nicht hier bearbeiten: Quelle ist {source}, danach `python tools/embed.py`.\n"
            f"{indent}{name}:\n{indent}  content: |\n{body}"
            f"{indent}# END embedded {name}\n")


def main(check=False):
    changed = []
    for compose in sorted(ROOT.glob("dev-unode-*/docker-compose.yml")):
        old = compose.read_text(encoding="utf-8")
        new = MARK.sub(lambda m: render(m.group(1), m.group(2), m.group(3), compose.parent), old)
        if new != old:
            changed.append(compose.relative_to(ROOT).as_posix())
            if not check:
                compose.write_text(new, encoding="utf-8", newline="\n")
    if check and changed:
        print("Nicht aktuell:", ", ".join(changed))
        return 1
    print("Aktualisiert:" if changed else "Alles aktuell.", ", ".join(changed))
    return 0


if __name__ == "__main__":
    sys.exit(main(check="--check" in sys.argv))
