#!/usr/bin/env python3

import argparse
import os
import re
import shutil
from pathlib import Path


SPEC2006_BENCHMARKS = [
    "400.perlbench",
    "401.bzip2",
    "403.gcc",
    "410.bwaves",
    "416.gamess",
    "429.mcf",
    "433.milc",
    "434.zeusmp",
    "435.gromacs",
    "436.cactusADM",
    "437.leslie3d",
    "444.namd",
    "445.gobmk",
    "447.dealII",
    "450.soplex",
    "453.povray",
    "454.calculix",
    "456.hmmer",
    "458.sjeng",
    "459.GemsFDTD",
    "462.libquantum",
    "464.h264ref",
    "465.tonto",
    "470.lbm",
    "471.omnetpp",
    "473.astar",
    "481.wrf",
    "482.sphinx3",
    "483.xalancbmk",
]


CHECKPOINT_RE = re.compile(r"^_(?P<slice>[^_]+)_(?P<weight>.+?)_?\.gz$")


def escape_spec_name(name):
    return name.replace(".", "_").replace("-", "_")


def toml_quote(value):
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def normalize_benchmark(name):
    query = name.lower().replace(".", "_").replace("-", "_")

    for benchmark in SPEC2006_BENCHMARKS:
        if name in (benchmark, escape_spec_name(benchmark)):
            return benchmark

    exact_matches = []
    fuzzy_matches = []
    for benchmark in SPEC2006_BENCHMARKS:
        escaped = escape_spec_name(benchmark).lower()
        short_name = escaped.split("_", 1)[1]
        if query == short_name:
            exact_matches.append(benchmark)
        elif query in short_name:
            fuzzy_matches.append(benchmark)

    matches = exact_matches or fuzzy_matches
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ValueError(
            f"ambiguous benchmark name {name!r}: " + ", ".join(matches)
        )

    return name


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Copy SPEC2006 checkpoint slices from result/<benchmark>/miao "
            "and dump checkpoint weights to cluster.toml."
        )
    )
    parser.add_argument(
        "destination",
        help=(
            "Destination directory. Checkpoints are copied under "
            "<destination>/spec2006_checkpoints."
        ),
    )
    parser.add_argument(
        "--result",
        default="result",
        help="Result link/directory created by nix build.",
    )
    parser.add_argument(
        "--toml",
        default=None,
        help=(
            "Output TOML path. Defaults to "
            "<destination>/spec2006_checkpoints/cluster.toml."
        ),
    )
    parser.add_argument(
        "--benchmarks",
        nargs="*",
        default=SPEC2006_BENCHMARKS,
        help=(
            "SPEC2006 benchmark names to copy. Both dotted, escaped and short "
            "names are accepted, e.g. 464.h264ref, 464_h264ref or h264."
        ),
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail if any requested benchmark has no checkpoints.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only print what would be copied and write no files.",
    )
    return parser.parse_args()


def find_checkpoints(result_path, benchmarks):
    requested = [normalize_benchmark(name) for name in benchmarks]
    escaped_to_name = {
        escape_spec_name(benchmark): benchmark
        for benchmark in requested
    }
    results = {benchmark: [] for benchmark in requested}

    for escaped_name, benchmark in escaped_to_name.items():
        miao_dir = result_path / escaped_name / "miao"
        if not miao_dir.is_dir():
            continue

        for slice_dir in sorted(miao_dir.iterdir(), key=lambda path: path.name):
            if not slice_dir.is_dir():
                continue

            for checkpoint in sorted(slice_dir.glob("*.gz"), key=lambda path: path.name):
                parsed = CHECKPOINT_RE.match(checkpoint.name)
                if parsed is None:
                    continue

                slice_name = parsed.group("slice")
                weight = parsed.group("weight")
                results[benchmark].append({
                    "slice": slice_name,
                    "weight": weight,
                    "source": checkpoint,
                })

    return results


def copy_checkpoints(results, checkpoint_root, dry_run=False):
    copied = 0

    for benchmark in sorted(results):
        escaped_name = escape_spec_name(benchmark)
        seen = set()

        for item in sorted(results[benchmark], key=lambda row: int(row["slice"])):
            if item["slice"] in seen:
                raise ValueError(
                    f"duplicate checkpoint slice for {benchmark}: {item['slice']}"
                )
            seen.add(item["slice"])

            destination_dir = checkpoint_root / escaped_name / item["slice"]
            destination = destination_dir / item["source"].name
            if dry_run:
                print(f"copy {item['source']} -> {destination}")
            else:
                destination_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item["source"], destination)
            copied += 1

    return copied


def render_toml(results):
    lines = [
        "# Generated by Deterload/dump_checkpoints.py",
        "",
    ]

    for benchmark in sorted(results):
        if not results[benchmark]:
            continue

        lines.append(f"[{toml_quote(benchmark)}]")
        for item in sorted(results[benchmark], key=lambda row: int(row["slice"])):
            lines.append(f"{toml_quote(item['slice'])} = {item['weight']}")
        lines.append("")

    return "\n".join(lines)


def main():
    args = parse_args()
    result_path = Path(args.result)
    if not result_path.exists():
        raise FileNotFoundError(f"result path does not exist: {result_path}")

    destination_root = Path(args.destination)
    checkpoint_root = destination_root / "spec2006_checkpoints"
    toml_path = Path(args.toml) if args.toml else checkpoint_root / "cluster.toml"

    results = find_checkpoints(result_path, args.benchmarks)
    missing = [name for name, items in results.items() if not items]
    if args.strict and missing:
        raise FileNotFoundError(
            "missing checkpoints for: " + ", ".join(sorted(missing))
        )

    copied = copy_checkpoints(results, checkpoint_root, dry_run=args.dry_run)
    if not args.dry_run:
        toml_path.parent.mkdir(parents=True, exist_ok=True)
        toml_path.write_text(render_toml(results), encoding="utf-8")

    found_benchmarks = sum(1 for items in results.values() if items)
    print(f"Found {found_benchmarks} benchmarks and {copied} checkpoints")
    if args.dry_run:
        print(f"Dry run only; TOML would be written to {toml_path}")
    else:
        print(f"Copied checkpoints to {checkpoint_root}")
        print(f"Wrote weights to {toml_path}")
    if missing:
        print("Missing benchmarks: " + ", ".join(sorted(missing)))


if __name__ == "__main__":
    main()
