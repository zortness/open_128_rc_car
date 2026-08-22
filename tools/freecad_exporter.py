#!/usr/bin/env python3
"""
freecad_exporter.py
-------------------
Modular FreeCAD CLI tool for Body discovery, interactive export configuration,
and automated release exporting (STL, STEP, 3MF, OBJ).

Subcommands:
  scan     - Scan .FCStd files and list all Body objects and their current export status.
  config   - Interactively (or via CLI flags) mark/select Bodies and export formats,
             persisting choices into export_config.json.
  export   - Headless export of all enabled Bodies into the output directory for releases.

Examples:
  python freecad_exporter.py scan
  python freecad_exporter.py config
  python freecad_exporter.py config --enable-all --format stl
  python freecad_exporter.py config --filter "Pan|Wheel|TPlate" --non-interactive
  python freecad_exporter.py export --outdir dist
  python freecad_exporter.py export --format-override step
"""

import os
import sys
import json
import glob
import re
import hashlib
import argparse
import subprocess

FC_PYTHON_CANDIDATES = [
    r"C:\Program Files\FreeCAD 1.1\bin\python.exe",
    r"C:\Program Files\FreeCAD\bin\python.exe",
    r"C:\Program Files (x86)\FreeCAD 1.1\bin\python.exe",
]


def ensure_freecad_environment():
    """Ensures FreeCAD modules can be imported, or re-executes with FreeCAD's Python."""
    try:
        import FreeCAD
        return
    except (ImportError, Exception):
        pass

    for cand in FC_PYTHON_CANDIDATES:
        if os.path.exists(cand):
            if os.path.normpath(sys.executable).lower() != os.path.normpath(cand).lower():
                args = [cand] + sys.argv
                try:
                    res = subprocess.run(args)
                    sys.exit(res.returncode)
                except Exception as e:
                    sys.stderr.write(f"Failed to launch FreeCAD Python: {e}\n")
                    sys.exit(1)

    sys.stderr.write(
        "Error: FreeCAD Python environment could not be found.\n"
        "Please run with FreeCAD's python.exe directly:\n"
        "  & \"C:\\Program Files\\FreeCAD 1.1\\bin\\python.exe\" freecad_exporter.py ...\n"
    )
    sys.exit(1)


# Bootstrap environment
ensure_freecad_environment()

import FreeCAD
import Part
import Mesh
import MeshPart

DEFAULT_CONFIG_FILE = "export_config.json"
DEFAULT_SOURCE_DIR = "..\\modular_car"
DEFAULT_OUTPUT_DIR = "..\\dist"
SUPPORTED_FORMATS = ["stl", "step", "3mf", "obj"]


def sanitize_filename(name):
    clean = "".join(c if c.isalnum() or c in ("-", "_", ".") else "_" for c in name)
    return clean.strip("_")


def load_config(config_path):
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[Warning] Failed to read {config_path}: {e}")
    return {"version": 1, "source_dir": DEFAULT_SOURCE_DIR, "output_dir": DEFAULT_OUTPUT_DIR, "items": {}}


def save_config(config, config_path):
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
    print(f"[Config] Saved export configuration to {config_path}")


def scan_bodies_in_directory(source_dir):
    """Scans all .FCStd files in source_dir and returns list of discoverable solid Body objects."""
    fc_files = sorted(glob.glob(os.path.join(source_dir, "*.FCStd")))
    discovered = []
    
    print(f"[Scan] Scanning {len(fc_files)} .FCStd files in '{source_dir}'...")
    
    for fpath in fc_files:
        fname = os.path.basename(fpath)
        # Skip assembly root or geometry reference by default
        if fname.startswith("00_"):
            continue
            
        try:
            doc = FreeCAD.openDocument(fpath)
            bodies = [o for o in doc.Objects if o.isDerivedFrom("PartDesign::Body")]
            
            for b in bodies:
                key = f"{fname}::{b.Name}"
                has_shape = hasattr(b, "Shape") and b.Shape is not None and not b.Shape.isNull()
                vol = round(b.Shape.Volume, 2) if has_shape else 0.0
                discovered.append({
                    "key": key,
                    "file": fname,
                    "full_path": os.path.abspath(fpath),
                    "object_name": b.Name,
                    "label": b.Label,
                    "type": b.TypeId,
                    "has_shape": has_shape,
                    "volume": vol
                })

            FreeCAD.closeDocument(doc.Name)
        except Exception as e:
            print(f"[Scan] Error reading {fname}: {e}")
            
    return discovered


def sync_discovered_with_config(discovered, config):
    """Syncs discovered bodies with existing config items, preserving user choices."""
    items = config.get("items", {})
    updated_items = {}
    
    for d in discovered:
        key = d["key"]
        existing = items.get(key, {})
        updated_items[key] = {
            "enabled": existing.get("enabled", False),
            "format": existing.get("format", "stl"),
            "file": d["file"],
            "object_name": d["object_name"],
            "label": d["label"],
            "output_name": existing.get("output_name", sanitize_filename(d["label"])),
            "volume": d["volume"],
            "type": d["type"]
        }
    config["items"] = updated_items
    return config


def cmd_scan(args):
    """Executes the scan command and prints a formatted summary table."""
    source_dir = args.dir or DEFAULT_SOURCE_DIR
    discovered = scan_bodies_in_directory(source_dir)
    config = load_config(args.config)
    config = sync_discovered_with_config(discovered, config)
    
    print("\n" + "=" * 92)
    print(f"DISCOVERED BODIES IN '{source_dir}'")
    print("=" * 92)
    print(f"{'#':<4} {'Status':<8} {'Format':<7} {'File':<28} {'Label':<28} {'Volume (mm³)':<12}")
    print("-" * 92)
    
    for idx, (key, item) in enumerate(config["items"].items(), 1):
        status = "[x] ON " if item["enabled"] else "[ ] OFF"
        fmt = item["format"].upper()
        print(f"{idx:<4} {status:<8} {fmt:<7} {item['file'][:26]:<28} {item['label'][:26]:<28} {item['volume']:<12}")
        
    enabled_count = sum(1 for item in config["items"].values() if item["enabled"])
    print("-" * 92)
    print(f"Total Bodies: {len(config['items'])} | Marked for Export: {enabled_count}")
    print("=" * 92 + "\n")


def parse_index_selection(user_input, max_len):
    """Parses selection tokens like '1, 3, 5-8', 'all', 'none' into indices."""
    tokens = user_input.replace(",", " ").split()
    indices = set()
    for tok in tokens:
        tok = tok.strip().lower()
        if not tok:
            continue
        if tok in ("all", "a", "*"):
            return set(range(max_len))
        if tok in ("none", "n", "clear"):
            return set()
        if "-" in tok:
            parts = tok.split("-")
            if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                start, end = int(parts[0]) - 1, int(parts[1]) - 1
                for i in range(max(0, start), min(max_len, end + 1)):
                    indices.add(i)
        elif tok.isdigit():
            i = int(tok) - 1
            if 0 <= i < max_len:
                indices.add(i)
    return indices


def interactive_config_loop(config, config_path):
    """Interactive text UI for selecting bodies and setting formats."""
    items_list = list(config["items"].items())
    
    while True:
        print("\n" + "=" * 96)
        print("INTERACTIVE EXPORT CONFIGURATION")
        print("=" * 96)
        print(f"{'#':<4} {'Status':<8} {'Format':<7} {'File':<28} {'Label':<26} {'Output File'}")
        print("-" * 96)
        
        for idx, (key, item) in enumerate(items_list, 1):
            status = "[x] ON " if item["enabled"] else "[ ] OFF"
            fmt = item["format"].upper()
            out_file = f"{item['output_name']}.{item['format']}"
            print(f"{idx:<4} {status:<8} {fmt:<7} {item['file'][:26]:<28} {item['label'][:24]:<26} {out_file}")
            
        enabled_count = sum(1 for k, item in items_list if item["enabled"])
        print("-" * 96)
        print(f"Total Bodies: {len(items_list)} | Selected for Export: {enabled_count}")
        print("=" * 96)
        print("Commands:")
        print("  <numbers/ranges>     : Toggle selection (e.g. '1, 3, 5-10')")
        print("  all / none           : Select all / Deselect all")
        print("  file <name>          : Toggle all bodies in matching file (e.g. 'file PanChassis')")
        print("  filter <regex>       : Select bodies matching label regex (e.g. 'filter Wheel|Gear')")
        print("  fmt <format> [ids]   : Set format (stl, step, 3mf, obj) for items (e.g. 'fmt step 1-4' or 'fmt stl all')")
        print("  name <id> <new_name> : Custom output filename for an item (e.g. 'name 1 Chassis_V1')")
        print("  outdir <path>        : Set output directory (current: " + config.get("output_dir", DEFAULT_OUTPUT_DIR) + ")")
        print("  save                 : Save configuration to disk and exit")
        print("  quit / q             : Exit without saving")
        print("-" * 96)
        
        try:
            cmd = input("Command > ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nExiting interactive configuration.")
            break
        if not cmd:
            continue
            
        cmd_lower = cmd.lower()
        if cmd_lower in ("save", "s"):
            save_config(config, config_path)
            print("Configuration saved successfully!")
            break
        elif cmd_lower in ("quit", "q", "exit"):
            print("Exiting without saving.")
            break
        elif cmd_lower in ("all", "a"):
            for k, item in items_list:
                item["enabled"] = True
            print("Selected all bodies.")
        elif cmd_lower in ("none", "n"):
            for k, item in items_list:
                item["enabled"] = False
            print("Deselected all bodies.")
        elif cmd_lower.startswith("filter "):
            pattern = cmd[7:].strip()
            try:
                rx = re.compile(pattern, re.IGNORECASE)
                match_count = 0
                for k, item in items_list:
                    if rx.search(item["label"]) or rx.search(item["file"]):
                        item["enabled"] = True
                        match_count += 1
                print(f"Enabled {match_count} bodies matching '{pattern}'.")
            except Exception as e:
                print(f"Invalid regex: {e}")
        elif cmd_lower.startswith("file "):
            f_query = cmd[5:].strip().lower()
            toggled = 0
            for k, item in items_list:
                if f_query in item["file"].lower():
                    item["enabled"] = not item["enabled"]
                    toggled += 1
            print(f"Toggled {toggled} bodies in files matching '{f_query}'.")
        elif cmd_lower.startswith("name "):
            parts = cmd.split(maxsplit=2)
            if len(parts) == 3 and parts[1].isdigit():
                idx = int(parts[1]) - 1
                if 0 <= idx < len(items_list):
                    clean_name = sanitize_filename(parts[2].strip())
                    items_list[idx][1]["output_name"] = clean_name
                    print(f"Set item #{parts[1]} output name to: {clean_name}")
                else:
                    print("Invalid item index.")
            else:
                print("Usage: name <id> <new_name>")
        elif cmd_lower.startswith("fmt "):
            parts = cmd.split(maxsplit=2)
            if len(parts) >= 2:
                new_fmt = parts[1].lower()
                if new_fmt not in SUPPORTED_FORMATS:
                    print(f"Unsupported format '{new_fmt}'. Choose from: {SUPPORTED_FORMATS}")
                else:
                    target_indices = parse_index_selection(parts[2], len(items_list)) if len(parts) > 2 else set(range(len(items_list)))
                    for i in target_indices:
                        items_list[i][1]["format"] = new_fmt
                    print(f"Updated format to '{new_fmt}' for {len(target_indices)} items.")
        elif cmd_lower.startswith("outdir "):
            new_out = cmd[7:].strip()
            if new_out:
                config["output_dir"] = new_out
                print(f"Output directory set to: {new_out}")
        else:
            indices = parse_index_selection(cmd, len(items_list))
            if indices:
                for i in indices:
                    items_list[i][1]["enabled"] = not items_list[i][1]["enabled"]
                print(f"Toggled {len(indices)} item(s).")
            else:
                print("Unrecognized command. Type 'save', 'all', 'none', numbers, or 'quit'.")


def cmd_config(args):
    """Executes the configuration mode (interactive by default unless --non-interactive is set)."""
    source_dir = args.dir or DEFAULT_SOURCE_DIR
    config_path = args.config or DEFAULT_CONFIG_FILE
    
    discovered = scan_bodies_in_directory(source_dir)
    config = load_config(config_path)
    config["source_dir"] = source_dir
    config = sync_discovered_with_config(discovered, config)
    
    # Process CLI flags if provided
    if args.enable_all:
        for item in config["items"].values():
            item["enabled"] = True
        print("[Config] Applied flag: Marked all bodies ON.")
    if args.disable_all:
        for item in config["items"].values():
            item["enabled"] = False
        print("[Config] Applied flag: Marked all bodies OFF.")
    if args.filter:
        rx = re.compile(args.filter, re.IGNORECASE)
        match_count = 0
        for item in config["items"].values():
            if rx.search(item["label"]) or rx.search(item["file"]):
                item["enabled"] = True
                match_count += 1
        print(f"[Config] Applied flag: Marked {match_count} bodies matching '{args.filter}' ON.")
    if args.format:
        fmt = args.format.lower()
        if fmt in SUPPORTED_FORMATS:
            for item in config["items"].values():
                item["format"] = fmt
            print(f"[Config] Applied flag: Set format to '{fmt}'.")
    if args.outdir:
        config["output_dir"] = args.outdir
        print(f"[Config] Applied flag: Set output directory to '{args.outdir}'.")
        
    # By default, run interactively unless --non-interactive is explicitly requested
    if args.non_interactive:
        save_config(config, config_path)
        print(f"[Config] Updated config saved with flags.")
    else:
        interactive_config_loop(config, config_path)


def export_shape_to_file(shape, out_path, fmt, linear_deflection=0.01, angular_deflection=0.174533):
    """Exports a TopoShape or Feature to the requested format."""
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    fmt = fmt.lower()
    
    if fmt in ("stl", "3mf", "obj"):
        mesh = MeshPart.meshFromShape(
            Shape=shape,
            LinearDeflection=linear_deflection,
            AngularDeflection=angular_deflection
        )
        mesh.write(out_path)
    elif fmt in ("step", "stp"):
        Part.export([shape], out_path)
    elif fmt == "brep":
        shape.exportBrep(out_path)
    else:
        raise ValueError(f"Unsupported format: {fmt}")


def compute_sha256(filepath):
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def cmd_export(args):
    """Executes the batch headless export process based on export_config.json."""
    config_path = args.config or DEFAULT_CONFIG_FILE
    config = load_config(config_path)
    
    source_dir = args.dir or config.get("source_dir", DEFAULT_SOURCE_DIR)
    out_dir = args.outdir or config.get("output_dir", DEFAULT_OUTPUT_DIR)
    
    items = config.get("items", {})
    enabled_items = [item for item in items.values() if item.get("enabled", False)]
    
    if not enabled_items:
        print(f"[Export] No bodies are marked for export in {config_path}.")
        print(f"         Run 'python freecad_exporter.py config' to select bodies.")
        return
        
    os.makedirs(out_dir, exist_ok=True)
    
    print("\n" + "=" * 70)
    print("FREECAD AUTOMATED RELEASE EXPORTER")
    print("=" * 70)
    print(f"Source Directory : {source_dir}")
    print(f"Output Directory : {out_dir}")
    print(f"Items to Export  : {len(enabled_items)}")
    print(f"Mesh Deflection  : Linear={args.linear_deflection}, Angular={args.angular_deflection}")
    print("=" * 70 + "\n")
    
    grouped = {}
    for item in enabled_items:
        grouped.setdefault(item["file"], []).append(item)
        
    exported_records = []
    total_exported = 0
    total_failed = 0
    
    file_idx = 0
    for fname, file_items in grouped.items():
        file_idx += 1
        src_path = os.path.join(source_dir, fname)
        if not os.path.exists(src_path):
            print(f"[{file_idx}/{len(grouped)}] [Error] File not found: {src_path}")
            total_failed += len(file_items)
            continue
            
        print(f"[{file_idx}/{len(grouped)}] Processing {fname} ({len(file_items)} bodies)...")
        
        try:
            doc = FreeCAD.openDocument(src_path)
            doc.recompute()
            
            for item in file_items:
                obj_name = item["object_name"]
                label = item["label"]
                fmt = args.format_override or item.get("format", "stl")
                out_name = f"{item.get('output_name', sanitize_filename(label))}.{fmt}"
                out_filepath = os.path.join(out_dir, out_name)
                
                obj = doc.getObject(obj_name)
                if not obj:
                    print(f"   [-] Object '{obj_name}' ('{label}') not found in doc.")
                    total_failed += 1
                    continue
                    
                shape = getattr(obj, "Shape", None)
                if not shape or shape.isNull():
                    print(f"   [-] Object '{label}' has null/empty shape.")
                    total_failed += 1
                    continue
                    
                try:
                    export_shape_to_file(
                        shape,
                        out_filepath,
                        fmt,
                        linear_deflection=args.linear_deflection,
                        angular_deflection=args.angular_deflection
                    )
                    size_kb = os.path.getsize(out_filepath) / 1024.0
                    sha256 = compute_sha256(out_filepath)
                    print(f"   [+] Exported '{label}' -> {out_name} ({size_kb:.1f} KB)")
                    
                    exported_records.append({
                        "file": fname,
                        "label": label,
                        "object_name": obj_name,
                        "format": fmt,
                        "output_file": out_name,
                        "size_bytes": os.path.getsize(out_filepath),
                        "sha256": sha256
                    })
                    total_exported += 1
                except Exception as ex:
                    print(f"   [-] Failed to export '{label}': {ex}")
                    total_failed += 1
                    
            FreeCAD.closeDocument(doc.Name)
        except Exception as e:
            print(f"   [Error] Failed to process document {fname}: {e}")
            total_failed += len(file_items)
            
    manifest_path = os.path.join(out_dir, "release_manifest.json")
    checksum_path = os.path.join(out_dir, "checksums.sha256")
    
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump({
            "generated_at": FreeCAD.Version()[0],
            "total_files": total_exported,
            "items": exported_records
        }, f, indent=2)
        
    with open(checksum_path, "w", encoding="utf-8") as f:
        for rec in exported_records:
            f.write(f"{rec['sha256']}  {rec['output_file']}\n")
            
    print("\n" + "=" * 70)
    print("EXPORT RUN COMPLETE!")
    print(f"Successfully Exported: {total_exported}")
    print(f"Failures / Missing   : {total_failed}")
    print(f"Release Manifest     : {manifest_path}")
    print(f"Checksums File       : {checksum_path}")
    print("=" * 70 + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="FreeCAD Automated Release & Component Export Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to execute")
    
    p_scan = subparsers.add_parser("scan", help="Scan FCStd files and list Body objects")
    p_scan.add_argument("--dir", default=DEFAULT_SOURCE_DIR, help="Source directory containing .FCStd files")
    p_scan.add_argument("--config", default=DEFAULT_CONFIG_FILE, help="Path to export_config.json")
    
    p_config = subparsers.add_parser("config", help="Configure export selections")
    p_config.add_argument("--dir", default=DEFAULT_SOURCE_DIR, help="Source directory containing .FCStd files")
    p_config.add_argument("--config", default=DEFAULT_CONFIG_FILE, help="Path to export_config.json")
    p_config.add_argument("--outdir", default=None, help="Set default output directory in config")
    p_config.add_argument("-i", "--interactive", action="store_true", help="Launch interactive selection menu")
    p_config.add_argument("--non-interactive", action="store_true", help="Do not prompt; save CLI flag updates")
    p_config.add_argument("--enable-all", action="store_true", help="Enable all discovered bodies")
    p_config.add_argument("--disable-all", action="store_true", help="Disable all discovered bodies")
    p_config.add_argument("--filter", help="Regex pattern to enable matching bodies")
    p_config.add_argument("--format", choices=SUPPORTED_FORMATS, help="Set export format for all items (stl, step, 3mf, obj)")
    
    p_export = subparsers.add_parser("export", help="Execute export operation")
    p_export.add_argument("--dir", help="Override source directory")
    p_export.add_argument("--outdir", help="Override output directory")
    p_export.add_argument("--config", default=DEFAULT_CONFIG_FILE, help="Path to export_config.json")
    p_export.add_argument("--format-override", choices=SUPPORTED_FORMATS, help="Force format for all exported items")
    p_export.add_argument("--linear-deflection", type=float, default=0.01, help="Mesh linear deflection (default: 0.01mm)")
    p_export.add_argument("--angular-deflection", type=float, default=0.174533, help="Mesh angular deflection in radians (default: 0.1745 ~ 10 deg)")
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(0)
        
    if args.command == "scan":
        cmd_scan(args)
    elif args.command == "config":
        cmd_config(args)
    elif args.command == "export":
        cmd_export(args)


if __name__ == "__main__":
    main()
