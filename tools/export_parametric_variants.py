#!/usr/bin/env python3
"""
export_parametric_variants.py
-----------------------------
Automated parametric variant exporter for FreeCAD.

Designed to iterate over a list/array of parameter values (such as `MountOffsetIncrements`
in `1410MotorMountVars`), recompute the object at each step, and export each variant to disk
(STL, STEP, 3MF, OBJ) with customizable filename suffixes.

Features:
- Automatically discovers VarSets and increment lists defined in FreeCAD models.
- Parametrically sets the target property and recomputes the body shape for each step.
- Guarantees the original model is restored to its initial state in all cases (via try/finally).
- Generates SHA256 checksums and a summary manifest for releases.
- Auto-bootstraps FreeCAD's Python environment.

Usage:
  python export_parametric_variants.py
  python export_parametric_variants.py --file modular_car/15_1410MotorFlatRear.FCStd --format stl
  python export_parametric_variants.py --increments 0,0.5,1.0,1.5,2.0,2.5,3.0 --outdir dist
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
        "  & \"C:\\Program Files\\FreeCAD 1.1\\bin\\python.exe\" export_parametric_variants.py ...\n"
    )
    sys.exit(1)


# Bootstrap environment
ensure_freecad_environment()

import FreeCAD
import Part
import MeshPart

DEFAULT_SOURCE_FILE = os.path.join("..", "modular_car", "15_1410MotorFlatRear.FCStd")
DEFAULT_OUTPUT_DIR = os.path.join("..", "dist")
SUPPORTED_FORMATS = ["stl", "step", "3mf", "obj"]


def sanitize_filename(name):
    clean = "".join(c if c.isalnum() or c in ("-", "_", ".") else "_" for c in name)
    return clean.strip("_")


def format_value_str(val):
    """Formats numeric value for clean filename representation (e.g. 1.0 -> '1mm', 1.5 -> '1.5mm')."""
    if isinstance(val, (int, float)):
        if float(val).is_integer():
            return f"{int(val)}"
        return f"{val}".rstrip('0').rstrip('.')
    return str(val).replace(" ", "")


def find_target_file(requested_file=None):
    """Locates the FreeCAD file, searching default locations if needed."""
    if requested_file and os.path.exists(requested_file):
        return os.path.abspath(requested_file)
        
    candidates = [
        os.path.join("modular_car", "15_1410MotorFlatRear.FCStd"),
        "15_1410MotorFlatRear.FCStd",
        os.path.join("..", "modular_car", "15_1410MotorFlatRear.FCStd"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return os.path.abspath(c)
            
    # Search recursively in current dir
    for root, dirs, files in os.walk("."):
        for f in files:
            if "1410MotorFlatRear" in f and f.endswith(".FCStd"):
                return os.path.abspath(os.path.join(root, f))
                
    return None


def export_shape(shape, out_path, fmt, linear_deflection=0.01, angular_deflection=0.174533):
    """Exports a TopoShape to the requested format."""
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    fmt = fmt.lower()
    
    if fmt in ("stl", "3mf", "obj"):
        mesh = MeshPart.meshFromShape(
            Shape=shape,
            LinearDeflection=linear_deflection,
            AngularDeflection=angular_deflection
        )
        mesh.write(out_path)
    elif fmt == "step":
        Part.export([shape], out_path)
    else:
        raise ValueError(f"Unsupported format: {fmt}")


def calculate_sha256(filepath):
    """Computes SHA256 hash of a file."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def export_variants(
    file_path,
    varset_name=None,
    param_name=None,
    increments=None,
    body_name=None,
    fmt="stl",
    output_dir=DEFAULT_OUTPUT_DIR,
    suffix_template="_offset_{val}mm",
    linear_deflection=0.01,
    angular_deflection=0.174533
):
    """
    Core function to export parametric variants for a given file and parameter.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Source file not found: {file_path}")
        
    print("\n" + "=" * 76)
    print("PARAMETRIC VARIANT EXPORTER")
    print("=" * 76)
    print(f"Source Document : {file_path}")
    print(f"Output Directory: {output_dir}")
    print(f"Export Format   : {fmt.upper()}")
    print("=" * 76 + "\n")
    
    os.makedirs(output_dir, exist_ok=True)
    doc = FreeCAD.openDocument(file_path)
    
    # 1. Discover VarSet
    varset_obj = None
    if varset_name:
        varset_obj = doc.getObject(varset_name)
    else:
        for o in doc.Objects:
            if o.Name == "1410MotorMountVars" or o.Label == "1410MotorMountVars":
                varset_obj = o
                break
        if not varset_obj:
            for o in doc.Objects:
                if "VarSet" in o.TypeId and (hasattr(o, "MountOffset") or hasattr(o, "MountOffsetIncrements")):
                    varset_obj = o
                    break
                    
    if not varset_obj:
        FreeCAD.closeDocument(doc.Name)
        raise ValueError(f"Could not find VarSet object (searched for '1410MotorMountVars' or properties)")
        
    # 2. Discover Parameter and Increments
    target_param = param_name or ("MountOffset" if hasattr(varset_obj, "MountOffset") else None)
    if not target_param or not hasattr(varset_obj, target_param):
        FreeCAD.closeDocument(doc.Name)
        raise AttributeError(f"VarSet '{varset_obj.Label}' does not have parameter '{target_param}'")
        
    inc_list = increments
    if inc_list is None:
        inc_prop_name = f"{target_param}Increments"
        if hasattr(varset_obj, inc_prop_name):
            inc_list = getattr(varset_obj, inc_prop_name)
        elif hasattr(varset_obj, "MountOffsetIncrements"):
            inc_list = getattr(varset_obj, "MountOffsetIncrements")
        else:
            inc_list = [0.0]
            
    if isinstance(inc_list, str):
        inc_list = [float(x.strip()) for x in inc_list.split(",") if x.strip()]
        
    initial_value = getattr(varset_obj, target_param)
    print(f"Found VarSet    : '{varset_obj.Label}' ({varset_obj.Name})")
    print(f"Target Parameter: '{target_param}' (Initial: {initial_value})")
    print(f"Increments      : {list(inc_list)} ({len(inc_list)} variants)")
    
    # 3. Discover Target Body
    target_body = None
    if body_name:
        target_body = doc.getObject(body_name)
        if not target_body:
            for o in doc.Objects:
                if o.Label == body_name:
                    target_body = o
                    break
    else:
        # Default to 1410MotorMount / Body060 or first body using the VarSet
        for o in doc.Objects:
            if o.isDerivedFrom("PartDesign::Body") and ("1410MotorMount" in o.Label or "1410MotorMount" in o.Name):
                target_body = o
                break
        if not target_body:
            bodies = [o for o in doc.Objects if o.isDerivedFrom("PartDesign::Body")]
            if bodies:
                target_body = bodies[-1]
                
    if not target_body:
        FreeCAD.closeDocument(doc.Name)
        raise ValueError("Could not find target PartDesign::Body to export.")
        
    target_body_label = target_body.Label
    print(f"Target Body     : '{target_body_label}' ({target_body.Name})\n")
    
    manifest_records = []
    
    # 4. Execute Parametric Iteration
    try:
        for idx, val in enumerate(inc_list, 1):
            val_formatted = format_value_str(val)
            suffix = suffix_template.format(val=val_formatted, idx=idx)
            clean_body_label = sanitize_filename(target_body_label)
            out_filename = f"{clean_body_label}{suffix}.{fmt}"
            out_filepath = os.path.join(output_dir, out_filename)
            
            # Apply value with unit handling
            if isinstance(initial_value, FreeCAD.Units.Quantity):
                unit_str = initial_value.UserString.split()[-1] if ' ' in initial_value.UserString else 'mm'
                setattr(varset_obj, target_param, FreeCAD.Units.Quantity(f"{val} {unit_str}"))
            else:
                setattr(varset_obj, target_param, val)
                
            doc.recompute()
            
            if target_body.Shape.isNull():
                print(f"  [{idx}/{len(inc_list)}] Variant {val_formatted}mm: [ERROR] Body produced a null shape!")
                continue
                
            vol = target_body.Shape.Volume
            bbox = target_body.Shape.BoundBox
            
            export_shape(
                shape=target_body.Shape,
                out_path=out_filepath,
                fmt=fmt,
                linear_deflection=linear_deflection,
                angular_deflection=angular_deflection
            )
            
            sha256 = calculate_sha256(out_filepath)
            size_kb = os.path.getsize(out_filepath) / 1024
            
            print(f"  [{idx}/{len(inc_list)}] {target_param}={val_formatted}mm -> {out_filename}")
            print(f"        Volume: {vol:.2f} mm³ | Bounds: {bbox.XLength:.1f}x{bbox.YLength:.1f}x{bbox.ZLength:.1f} mm | Size: {size_kb:.1f} KB")
            
            manifest_records.append({
                "index": idx,
                "parameter": target_param,
                "value": val,
                "unit": "mm",
                "output_file": out_filename,
                "path": out_filepath,
                "volume_mm3": round(vol, 3),
                "bounding_box": {
                    "length": round(bbox.XLength, 2),
                    "width": round(bbox.YLength, 2),
                    "height": round(bbox.ZLength, 2)
                },
                "sha256": sha256
            })
            
    finally:
        # Guarantee restoration of original state
        print(f"\n[Cleanup] Restoring original {target_param} to: {initial_value}")
        setattr(varset_obj, target_param, initial_value)
        doc.recompute()
        doc.save()
        FreeCAD.closeDocument(doc.Name)
        print("[Cleanup] Document saved and restored to original state.")
        
    # Write variant manifest
    manifest_path = os.path.join(output_dir, "variants_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump({
            "source_file": file_path,
            "target_body": target_body_label,
            "parameter": target_param,
            "initial_value": str(initial_value),
            "variants_count": len(manifest_records),
            "variants": manifest_records
        }, f, indent=2)
        
    print(f"\n[Manifest] Saved variants manifest: {manifest_path}")
    print("=" * 76 + "\n")
    return manifest_records


def main():
    parser = argparse.ArgumentParser(
        description="FreeCAD Parametric Variant Exporter",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--file", help="Path to .FCStd file (default: modular_car/15_1410MotorFlatRear.FCStd)")
    parser.add_argument("--varset", help="Name of VarSet object (default: auto-detect '1410MotorMountVars')")
    parser.add_argument("--param", default="MountOffset", help="Target parameter name (default: MountOffset)")
    parser.add_argument("--increments", help="Comma-separated list of increment values (overrides VarSet array)")
    parser.add_argument("--body", help="Name/Label of PartDesign::Body to export (default: auto-detect)")
    parser.add_argument("--format", choices=SUPPORTED_FORMATS, default="stl", help="Export format (default: stl)")
    parser.add_argument("--outdir", default=DEFAULT_OUTPUT_DIR, help="Output directory (default: dist)")
    parser.add_argument("--suffix-template", default="_offset_{val}mm", help="Suffix template (default: '_offset_{val}mm')")
    parser.add_argument("--linear-deflection", type=float, default=0.01, help="Linear deflection for mesh export (default: 0.01)")
    parser.add_argument("--angular-deflection", type=float, default=0.174533, help="Angular deflection for mesh export (default: ~10 deg)")
    
    args = parser.parse_args()
    
    target_file = find_target_file(args.file)
    if not target_file:
        sys.stderr.write(f"Error: Could not locate FreeCAD source file '{args.file or DEFAULT_SOURCE_FILE}'.\n")
        sys.exit(1)
        
    inc_override = None
    if args.increments:
        inc_override = [float(x.strip()) for x in args.increments.split(",") if x.strip()]
        
    export_variants(
        file_path=target_file,
        varset_name=args.varset,
        param_name=args.param,
        increments=inc_override,
        body_name=args.body,
        fmt=args.format,
        output_dir=args.outdir,
        suffix_template=args.suffix_template,
        linear_deflection=args.linear_deflection,
        angular_deflection=args.angular_deflection
    )


if __name__ == "__main__":
    main()
