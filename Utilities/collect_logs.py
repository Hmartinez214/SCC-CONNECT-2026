#!/usr/bin/env python3
"""
collect_logs.py

Recursively walks a root directory (e.g. a folder of MFC run subfolders)
and copies (or moves) log/output and error files into a single output
directory, organized into two subfolders.

Example:
    Given:
        mfc-runs/
            2D_shockbubble/
                shockdroplet-n64.out
                shockdroplet-n64.err
            3D_shockdroplet0/
                shockdroplet-n32.out
                shockdroplet-n32.err
            3D_shockdroplet1/
                MFC.out
                MFC.err

    Running:
        python3 collect_logs.py mfc-runs/ -o collected_logs/

    Produces:
        collected_logs/
            logs/
                shockdroplet-n64.out
                shockdroplet-n32.out
                3D_shockdroplet1__MFC.out
            errs/
                shockdroplet-n64.err
                shockdroplet-n32.err
                3D_shockdroplet1__MFC.err

    (Files with a colliding name, like the repeated "MFC.out"/"MFC.err"
    seen across different case folders, get prefixed with their parent
    folder's name instead of silently overwriting each other.)

Usage:
    python3 collect_logs.py <source_dir> [-o OUTPUT] [--move] [--dry-run]
                             [--log-ext out log] [--err-ext err]
                             [--log-subdir logs] [--err-subdir errs]

By default this COPIES files (originals are left in place). Pass --move
to relocate them instead. Pass --dry-run to preview what would happen
without touching any files.
"""

import argparse
import shutil
import sys
from pathlib import Path


def make_unique(dest_dir: Path, source_path: Path) -> Path:
    """
    Build a non-clobbering destination path for a file whose name already
    exists in dest_dir, by prefixing it with its parent folder's name
    (e.g. 'MFC.out' from '3D_shockdroplet1/' becomes
    '3D_shockdroplet1__MFC.out'). Falls back to a numeric suffix in the
    rare case that still collides.
    """
    parent_name = source_path.parent.name
    candidate = dest_dir / f"{parent_name}__{source_path.name}"

    if not candidate.exists():
        return candidate

    i = 1
    while True:
        candidate = dest_dir / f"{parent_name}__{i}__{source_path.name}"
        if not candidate.exists():
            return candidate
        i += 1


def collect_files(source_dir: Path, output_dir: Path,
                   log_exts, err_exts,
                   log_subdir_name: str, err_subdir_name: str,
                   move: bool = False, dry_run: bool = False) -> None:
    log_dir = output_dir / log_subdir_name
    err_dir = output_dir / err_subdir_name

    if not dry_run:
        log_dir.mkdir(parents=True, exist_ok=True)
        err_dir.mkdir(parents=True, exist_ok=True)

    log_exts = {e.lower().lstrip(".") for e in log_exts}
    err_exts = {e.lower().lstrip(".") for e in err_exts}

    action = shutil.move if move else shutil.copy2
    action_word = "Moved" if move else "Copied"

    n_logs = n_errs = 0

    for path in sorted(source_dir.rglob("*")):
        if not path.is_file():
            continue

        # Skip anything already inside the output directory, in case
        # output_dir happens to live underneath source_dir. Both sides
        # must be resolved (absolute) for relative_to() to correctly
        # detect containment -- comparing a relative path against a
        # resolved one always raises ValueError, which would silently
        # disable this check entirely.
        try:
            path.resolve().relative_to(output_dir.resolve())
            continue
        except ValueError:
            pass

        ext = path.suffix.lower().lstrip(".")

        if ext in log_exts:
            dest_dir = log_dir
        elif ext in err_exts:
            dest_dir = err_dir
        else:
            continue

        dest_path = dest_dir / path.name
        if dest_path.exists():
            dest_path = make_unique(dest_dir, path)

        if dry_run:
            verb = "move" if move else "copy"
            print(f"[dry-run] Would {verb}: {path} -> {dest_path}")
        else:
            action(str(path), str(dest_path))
            print(f"{action_word}: {path} -> {dest_path}")

        if ext in log_exts:
            n_logs += 1
        else:
            n_errs += 1

    print(f"\nDone. {n_logs} log file(s), {n_errs} err file(s) processed.")


def main():
    parser = argparse.ArgumentParser(
        description="Collect log/output and error files from subdirectories "
                     "into one output folder with separate 'logs' and "
                     "'errs' subfolders."
    )
    parser.add_argument(
        "source", type=Path,
        help="Root directory to search recursively (e.g. your mfc-runs/ folder)."
    )
    parser.add_argument(
        "-o", "--output", type=Path, default=Path("collected_logs"),
        help="Output directory to create/use (default: ./collected_logs)"
    )
    parser.add_argument(
        "--log-ext", nargs="+", default=["out", "log"],
        help="File extensions treated as 'log' files, without dots (default: out log)"
    )
    parser.add_argument(
        "--err-ext", nargs="+", default=["err"],
        help="File extensions treated as 'err' files, without dots (default: err)"
    )
    parser.add_argument(
        "--log-subdir", default="logs",
        help="Name of the output subfolder for log files (default: logs)"
    )
    parser.add_argument(
        "--err-subdir", default="errs",
        help="Name of the output subfolder for err files (default: errs)"
    )
    parser.add_argument(
        "--move", action="store_true",
        help="Move files instead of copying (default: copy; originals are left in place)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview what would happen without touching any files"
    )

    args = parser.parse_args()

    if not args.source.is_dir():
        print(f"Error: '{args.source}' is not a directory.", file=sys.stderr)
        sys.exit(1)

    collect_files(
        source_dir=args.source,
        output_dir=args.output,
        log_exts=args.log_ext,
        err_exts=args.err_ext,
        log_subdir_name=args.log_subdir,
        err_subdir_name=args.err_subdir,
        move=args.move,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
