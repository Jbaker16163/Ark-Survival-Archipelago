#!/usr/bin/env python3
"""Package the ark_ase world into dist/ark_ase.apworld.

Includes the zip-root manifest (apworld/archipelago.json) + the ark_ase package
(minus __pycache__/.pyc). Run after editing the world or regenerating data files.

  python tools/build_apworld.py
"""
import os
import zipfile

HERE = os.path.dirname(__file__)
ROOT = os.path.normpath(os.path.join(HERE, ".."))
SRC = os.path.join(ROOT, "apworld")
OUT = os.path.join(ROOT, "dist", "ark_ase.apworld")


def main() -> None:
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(os.path.join(SRC, "archipelago.json"), "archipelago.json")   # manifest at zip root
        for dp, _, fs in os.walk(os.path.join(SRC, "ark_ase")):
            if "__pycache__" in dp:
                continue
            for f in fs:
                if f.endswith(".pyc"):
                    continue
                full = os.path.join(dp, f)
                z.write(full, os.path.relpath(full, SRC))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
