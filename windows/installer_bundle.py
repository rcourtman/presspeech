"""Describe the exact immutable payload consumed by installer.iss."""
import argparse
import hashlib
from pathlib import Path
import re


PREFIX = "_presspeech-"
MANIFEST = "presspeech-bundle.sha256"
OWNER = "presspeech-bundle.owner"
PENDING = "Presspeech.exe.pending"
RESERVED = {MANIFEST.casefold(), OWNER.casefold(), PENDING.casefold()}


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def bundle_name(executable):
    # Read the same option the pinned bootloader consumes, including for a
    # reused or signed package. Never infer it from a directory listing.
    from PyInstaller.archive.readers import CArchiveReader
    options = CArchiveReader(str(executable)).options
    names = [value.removeprefix("pyi-contents-directory ") for value in options
             if value.startswith("pyi-contents-directory ")]
    if len(names) != 1 or not re.fullmatch(r"_presspeech-[0-9a-f]{32}", names[0]):
        raise ValueError("Executable has no unique Presspeech bundle identity")
    return names[0]


def safe_relative(path):
    parts = path.parts
    if not parts or path.is_absolute():
        raise ValueError("Invalid bundle path")
    for part in parts:
        stem = part.split(".", 1)[0].upper()
        if (part in {".", ".."} or part.endswith((".", " "))
                or re.search(r'[<>:"/\\|?*\x00-\x1f]', part)
                or stem in {"CON", "PRN", "AUX", "NUL"}
                or re.fullmatch(r"(?:COM|LPT)[0-9]", stem)):
            raise ValueError(f"Unsafe Windows bundle path: {path}")
    return "\\".join(parts)


def prepare(root, include):
    root = root.resolve()
    executable = root / "Presspeech.exe"
    name = bundle_name(executable)
    bundle = root / name
    if {p.name for p in root.iterdir()} != {"Presspeech.exe", name}:
        raise ValueError("Package must contain exactly the executable and its bundle")
    if executable.is_symlink() or bundle.is_symlink() or not bundle.is_dir():
        raise ValueError("Package paths must be regular files and directories")
    entries = []
    seen = set()
    size = executable.stat().st_size
    longest = 0
    for path in sorted(bundle.rglob("*")):
        if path.is_symlink() or getattr(path.lstat(), "st_file_attributes", 0) & 0x400:
            raise ValueError("Reparse points are not supported in installer bundles")
        if path.is_dir():
            continue
        relative = path.relative_to(bundle)
        if len(relative.parts) == 1 and relative.name.casefold() == PENDING.casefold():
            raise ValueError('Package contains a staged executable from an installation')
        if len(relative.parts) == 1 and relative.name.casefold() in RESERVED:
            continue
        value = safe_relative(relative)
        if value.casefold() in seen:
            raise ValueError("Case-colliding bundle paths")
        seen.add(value.casefold())
        size += path.stat().st_size
        longest = max(longest, len(value))
        entries.append(f"{digest(path)}  {value}")
    if not entries:
        raise ValueError("Bundle is empty")
    manifest = bundle / MANIFEST
    manifest.write_text("\n".join(entries) + "\n", encoding="utf-8-sig", newline="\n")
    # A marker authenticates ownership of an interrupted staging directory.
    # The installer writes it before copying any payload and checks it before
    # recovering or deleting an inactive generation.
    owner = bundle / OWNER
    owner.write_text(name, encoding="ascii")
    size += manifest.stat().st_size + owner.stat().st_size
    metadata = {
        "BundleDir": name,
        "BundleBytes": str(size),
        "ExeBytes": str(executable.stat().st_size),
        "ExeSHA256": digest(executable),
        "ManifestSHA256": digest(manifest),
        "MaxBundlePath": str(max(longest, len(PENDING), len(MANIFEST))),
    }
    include.parent.mkdir(parents=True, exist_ok=True)
    include.write_text("".join(f'#define {key} "{value}"\n' for key, value in metadata.items()),
                       encoding="utf-8")
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--include", type=Path, required=True)
    args = parser.parse_args()
    metadata = prepare(args.package, args.include)
    print(f"Verified installer payload: {metadata['BundleDir']}, {metadata['BundleBytes']} bytes")


if __name__ == "__main__":
    main()
