"""One-off repair: undo cp1252 mojibake in source files re-saved with the wrong encoding."""
import pathlib
import shutil
from encodings import cp1252

TARGETS = [
    "strategies/fomo_trade.py",
    "strategies/orders_menu.py",
    "strategies/risk_management.py",
    "strategies/status.py",
]

# cp1252 leaves 5 byte slots undefined; the corrupting decoder mapped them to C1 controls.
REVERSE = {ch: i for i, ch in enumerate(cp1252.decoding_table) if ch != "\ufffe"}
REVERSE.update({chr(b): b for b in (0x81, 0x8D, 0x8F, 0x90, 0x9D)})


def repair(path: pathlib.Path) -> None:
    text = pathlib.Path(path).read_text(encoding="utf-8").lstrip("\ufeff")
    missing = sorted({c for c in text if c not in REVERSE})
    if missing:
        raise SystemExit(f"{path}: cannot round-trip {[hex(ord(c)) for c in missing]}")
    fixed = bytes(REVERSE[c] for c in text).decode("utf-8")
    shutil.copyfile(path, path.with_suffix(path.suffix + ".bak"))
    path.write_text(fixed, encoding="utf-8", newline="\n")
    print(f"repaired {path}")


for target in TARGETS:
    repair(pathlib.Path(target))
