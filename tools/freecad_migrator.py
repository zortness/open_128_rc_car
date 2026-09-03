#!/usr/bin/env python3
"""
freecad_migrator.py
-------------------
Generic, configurable migration and modularization framework for FreeCAD.

Key Capabilities:
1. inspect: Analyzes dependencies (OutList, Expressions, Binders, Clones, Origins) for any object or file.
2. plan   : Discovers all Parts, Bodies, and VarSets across source files, computes the dependency DAG
            and topological tiers, and generates a customizable `migration_plan.json`.
3. migrate: Executes the migration plan tier-by-tier, automatically reparenting SubShapeBinders,
            linking VarSets, eliminating duplicates, and generating a master assembly.

Examples:
  python freecad_migrator.py plan --source "128 Mini Car.FCStd" --outdir modular_car
  python freecad_migrator.py inspect --source "128 Mini Car.FCStd" --object Body048
  python freecad_migrator.py migrate --plan migration_plan.json
"""

import os
import sys
import json
import glob
import re
import shutil
import argparse
import subprocess
from collections import defaultdict, deque

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
        "  & \"C:\\Program Files\\FreeCAD 1.1\\bin\\python.exe\" freecad_migrator.py ...\n"
    )
    sys.exit(1)


# Bootstrap FreeCAD environment
ensure_freecad_environment()

import FreeCAD
import Part
import MeshPart

DEFAULT_PLAN_FILE = "migration_plan.json"
DEFAULT_SOURCE_FILE = "128 Mini Car.FCStd"
DEFAULT_OUTPUT_DIR = "modular_car"


def sanitize_filename(name):
    clean = "".join(c if c.isalnum() or c in ("-", "_", ".") else "_" for c in name)
    return clean.strip("_")


def collect_descendants_and_features(obj, visited=None, excluded_names=None):
    """Recursively collects an object, its children, origins, axes, planes, and internal features."""
    if visited is None:
        visited = set()
    if excluded_names is None:
        excluded_names = set()
        
    if obj in visited or obj.Name in excluded_names:
        return visited
    visited.add(obj)
    
    # 1. Group children
    if hasattr(obj, 'Group'):
        for c in obj.Group:
            if c.Name not in excluded_names:
                collect_descendants_and_features(c, visited, excluded_names)
                
    # 2. Origin coordinate system
    if hasattr(obj, 'Origin') and obj.Origin:
        collect_descendants_and_features(obj.Origin, visited, excluded_names)
        
    # 3. Origin sub-features (axes, planes, points)
    if hasattr(obj, 'OriginFeatures'):
        doc = getattr(obj, 'Document', None)
        for feat in obj.OriginFeatures:
            if isinstance(feat, tuple) and doc:
                feat_obj = doc.getObject(feat[0])
                if feat_obj and feat_obj.Name not in excluded_names:
                    collect_descendants_and_features(feat_obj, visited, excluded_names)
            elif hasattr(feat, 'Name') and feat.Name not in excluded_names:
                collect_descendants_and_features(feat, visited, excluded_names)
                
    # 4. OutList features
    if hasattr(obj, 'OutList'):
        for out in obj.OutList:
            if out.Name in excluded_names:
                continue
            if out.isDerivedFrom("PartDesign::Feature") or \
               out.isDerivedFrom("Sketcher::SketchObject") or \
               out.isDerivedFrom("App::OriginGroupExtension") or \
               out.isDerivedFrom("App::Line") or \
               out.isDerivedFrom("App::Plane") or \
               out.isDerivedFrom("App::Point") or \
               out.isDerivedFrom("Part::Feature"):
                collect_descendants_and_features(out, visited, excluded_names)
                
    return visited


def get_root_parts(doc):
    all_parts = [o for o in doc.Objects if o.isDerivedFrom("App::Part")]
    root_parts = []
    for p in all_parts:
        is_nested = False
        for parent in p.InList:
            if parent.isDerivedFrom("App::Part") and hasattr(parent, 'Group') and p in parent.Group:
                is_nested = True
                break
        if not is_nested:
            root_parts.append(p)
    return root_parts


def analyze_document_graph(doc):
    """Builds a comprehensive dependency graph of all objects in a FreeCAD document."""
    graph = {
        "objects": {},
        "dependencies": defaultdict(set), # obj_name -> set of obj_names it depends on
        "reverse_deps": defaultdict(set), # obj_name -> set of obj_names that depend on it
        "binder_supports": {}, # binder_name -> [(target_name, subelements)]
        "root_parts": [],
        "root_bodies": [],
        "varsets": []
    }
    
    for obj in doc.Objects:
        graph["objects"][obj.Name] = {
            "name": obj.Name,
            "label": obj.Label,
            "type": obj.TypeId,
            "has_shape": hasattr(obj, "Shape") and obj.Shape is not None and not obj.Shape.isNull(),
            "volume": round(obj.Shape.Volume, 2) if hasattr(obj, "Shape") and obj.Shape and not obj.Shape.isNull() else 0.0
        }
        
        if "VarSet" in obj.TypeId or obj.Name == "VarSet":
            graph["varsets"].append(obj.Name)
            
        # 1. OutList dependencies
        for target in getattr(obj, "OutList", []):
            if target != obj:
                graph["dependencies"][obj.Name].add(target.Name)
                graph["reverse_deps"][target.Name].add(obj.Name)
                
        # 2. ExpressionEngine dependencies
        for prop, expr in getattr(obj, "ExpressionEngine", []):
            for other in doc.Objects:
                if other != obj and (other.Name in expr or f"<<{other.Label}>>" in expr):
                    graph["dependencies"][obj.Name].add(other.Name)
                    graph["reverse_deps"][other.Name].add(obj.Name)
                    
        # 3. SubShapeBinder dependencies
        if "Binder" in obj.TypeId:
            supp = getattr(obj, "Support", None)
            if supp:
                mapped = []
                for s in supp:
                    target_name = s[0].Name if hasattr(s[0], "Name") else str(s[0])
                    sub = s[1] if len(s) > 1 else ('',)
                    mapped.append((target_name, sub))
                    graph["dependencies"][obj.Name].add(target_name)
                    graph["reverse_deps"][target_name].add(obj.Name)
                graph["binder_supports"][obj.Name] = mapped
                
        # 4. Clone dependencies
        if hasattr(obj, "CloneOf") and obj.CloneOf:
            c_name = obj.CloneOf.Name
            graph["dependencies"][obj.Name].add(c_name)
            graph["reverse_deps"][c_name].add(obj.Name)

    graph["root_parts"] = [p.Name for p in get_root_parts(doc)]
    graph["root_bodies"] = [b.Name for b in doc.Objects if b.isDerivedFrom("PartDesign::Body") and not any(p.isDerivedFrom("App::Part") and b in getattr(p, 'Group', []) for p in doc.Objects)]
    
    return graph


def compute_topological_tiers(destinations, item_dependencies):
    """
    Computes execution order (tiers) for destination files based on inter-file dependencies.
    """
    # Map each object to its destination file
    obj_to_file = {}
    for filename, file_def in destinations.items():
        for item_name in file_def.get("items", []):
            obj_to_file[item_name] = filename
            
    # Compute file-level dependency DAG
    file_deps = defaultdict(set)
    file_in_degree = defaultdict(int)
    all_files = set(destinations.keys())
    
    for obj_name, target_file in obj_to_file.items():
        for dep_obj in item_dependencies.get(obj_name, []):
            dep_file = obj_to_file.get(dep_obj)
            if dep_file and dep_file != target_file:
                file_deps[dep_file].add(target_file)
                
    for f in all_files:
        file_in_degree[f] = 0
    for src_file, targets in file_deps.items():
        for tgt in targets:
            file_in_degree[tgt] += 1
            
    # Kahn's algorithm for topological tiers
    queue = deque([f for f in all_files if file_in_degree[f] == 0])
    tiers = []
    visited_count = 0
    
    while queue:
        current_tier = []
        for _ in range(len(queue)):
            f = queue.popleft()
            current_tier.append(f)
            visited_count += 1
            for dependent in file_deps.get(f, []):
                file_in_degree[dependent] -= 1
                if file_in_degree[dependent] == 0:
                    queue.append(dependent)
        tiers.append(sorted(current_tier))
        
    if visited_count < len(all_files):
        # Circular dependency detected among some files; append remaining
        remaining = [f for f in all_files if f not in [item for tier in tiers for item in tier]]
        if remaining:
            print(f"[Warning] Cyclic file dependencies detected among: {remaining}")
            tiers.append(sorted(remaining))
            
    return tiers


def generate_migration_plan(source_file, output_dir, custom_mappings=None):
    """Builds a default migration plan JSON with smart component breakout."""
    print(f"[Plan] Analyzing source document: {source_file}...")
    doc = FreeCAD.openDocument(source_file)
    graph = analyze_document_graph(doc)
    
    destinations = {}
    
    # 1. Master GeometryReference & VarSet
    destinations["00_GeometryReference.FCStd"] = {
        "description": "Master Parameters (VarSet) and Reference Sketches",
        "is_reference": True,
        "items": graph["varsets"] + [p for p in graph["root_parts"] if "geom" in doc.getObject(p).Label.lower() or p == "Part"]
    }
    
    # 2. Key Shared Components (RearPanPlateFlatMount, etc.)
    if doc.getObject("Body033"):
        destinations["00_RearPanPlateFlatMount.FCStd"] = {
            "description": "Shared Rear Pan Plate Mount",
            "items": ["Body033"]
        }
        
    # 3. Component Parts
    ref_items = set(destinations["00_GeometryReference.FCStd"]["items"])
    if "00_RearPanPlateFlatMount.FCStd" in destinations:
        ref_items.update(destinations["00_RearPanPlateFlatMount.FCStd"]["items"])
        
    idx = 1
    for p_name in graph["root_parts"]:
        if p_name in ref_items:
            continue
        p_obj = doc.getObject(p_name)
        clean_lbl = sanitize_filename(p_obj.Label)
        fname = f"{idx:02d}_{clean_lbl}.FCStd"
        destinations[fname] = {
            "description": f"Part: {p_obj.Label}",
            "items": [p_name]
        }
        idx += 1
        
    # Apply user-customized mappings if provided (e.g. moving Body048 to 13_ThreadedRearAxle.FCStd)
    if custom_mappings:
        for obj_name, target_file in custom_mappings.items():
            # Remove obj_name from existing destinations
            for d in destinations.values():
                if obj_name in d.get("items", []):
                    d["items"].remove(obj_name)
            # Add to target_file
            if target_file not in destinations:
                destinations[target_file] = {"description": f"Custom Destination: {target_file}", "items": []}
            if obj_name not in destinations[target_file]["items"]:
                destinations[target_file]["items"].append(obj_name)
                
    # Compute topological tiers
    tiers = compute_topological_tiers(destinations, graph["dependencies"])
    
    plan = {
        "version": 1,
        "source_file": os.path.abspath(source_file),
        "output_dir": output_dir,
        "assembly_file": "00_CarAssembly.FCStd",
        "destinations": destinations,
        "execution_tiers": tiers
    }
    
    FreeCAD.closeDocument(doc.Name)
    return plan


def execute_migration_plan(plan_path):
    """Executes the migration plan tier-by-tier with automated rebinding and cleanup."""
    with open(plan_path, "r", encoding="utf-8") as f:
        plan = json.load(f)
        
    source_file = plan["source_file"]
    output_dir = plan.get("output_dir", DEFAULT_OUTPUT_DIR)
    destinations = plan["destinations"]
    tiers = plan.get("execution_tiers", [list(destinations.keys())])
    assembly_filename = plan.get("assembly_file", "00_CarAssembly.FCStd")
    
    os.makedirs(output_dir, exist_ok=True)
    
    print("\n" + "=" * 76)
    print("FREECAD GENERIC MIGRATION ENGINE")
    print("=" * 76)
    print(f"Source Document : {source_file}")
    print(f"Output Directory: {output_dir}")
    print(f"Total Targets   : {len(destinations)} files across {len(tiers)} execution tiers")
    print("=" * 76 + "\n")
    
    # 1. Map each object to its assigned target file
    obj_to_file = {}
    file_to_items = {}
    for filename, file_def in destinations.items():
        file_to_items[filename] = file_def.get("items", [])
        for item in file_def.get("items", []):
            obj_to_file[item] = filename
            
    # Open source to collect all binder mappings
    doc_src = FreeCAD.openDocument(source_file)
    binder_map = {}
    for o in doc_src.Objects:
        if "Binder" in o.TypeId:
            supp = getattr(o, "Support", None)
            if supp:
                binder_map[o.Name] = [(s[0].Name if hasattr(s[0], "Name") else str(s[0]), s[1] if len(s) > 1 else ('',)) for s in supp]
    
    # Find master reference document (contains VarSet)
    ref_filename = None
    for fname, fdef in destinations.items():
        if fdef.get("is_reference") or any("varset" in it.lower() for it in fdef.get("items", [])):
            ref_filename = fname
            break
    if not ref_filename:
        ref_filename = "00_GeometryReference.FCStd"
        
    ref_file_path = os.path.join(output_dir, ref_filename)
    FreeCAD.closeDocument(doc_src.Name)
    
    created_files = []
    
    # 2. Execute Tier-by-Tier
    tier_idx = 0
    for tier in tiers:
        tier_idx += 1
        print(f"\n--- EXECUTING TIER {tier_idx}/{len(tiers)}: {tier} ---")
        
        for filename in tier:
            target_path = os.path.join(output_dir, filename)
            file_def = destinations.get(filename, {})
            assigned_items = file_def.get("items", [])
            is_ref = file_def.get("is_reference", False) or filename == ref_filename
            
            print(f"  * Generating '{filename}' with items: {assigned_items}...")
            
            if os.path.exists(target_path):
                os.remove(target_path)
            shutil.copyfile(source_file, target_path)
            
            doc_target = FreeCAD.openDocument(target_path)
            
            # Determine objects to preserve
            # Exclude items assigned to OTHER files to avoid duplicates
            excluded_from_this_file = set()
            for other_file, other_items in file_to_items.items():
                if other_file != filename:
                    for it in other_items:
                        excluded_from_this_file.add(it)
                        
            keep_objs = set()
            for item_name in assigned_items:
                item_obj = doc_target.getObject(item_name)
                if item_obj:
                    collect_descendants_and_features(item_obj, keep_objs, excluded_names=excluded_from_this_file)
                    
            # Maintain cache of opened external documents for this file
            open_ext_docs = {}
            def get_ext_doc(d_file):
                if d_file not in open_ext_docs:
                    d_path = os.path.join(output_dir, d_file)
                    if os.path.exists(d_path):
                        open_ext_docs[d_file] = FreeCAD.openDocument(d_path)
                return open_ext_docs.get(d_file)

            # Reparent Binders pointing to objects outside this file
            for o in list(doc_target.Objects):
                if o.Name in binder_map and o in keep_objs:
                    new_supp = []
                    for target_obj_name, subelems in binder_map[o.Name]:
                        dest_file = obj_to_file.get(target_obj_name)
                        if dest_file and dest_file != filename:
                            # Rebind to external document
                            doc_ext = get_ext_doc(dest_file)
                            if doc_ext:
                                ext_obj = doc_ext.getObject(target_obj_name)
                                if ext_obj:
                                    new_supp.append((ext_obj, subelems))
                        else:
                            # Internal reference
                            local_tgt = doc_target.getObject(target_obj_name)
                            if local_tgt:
                                new_supp.append((local_tgt, subelems))
                    if new_supp:
                        o.Support = new_supp
                        
            # Delete non-keep objects
            to_delete = [o for o in doc_target.Objects if o not in keep_objs]
            for o in reversed(to_delete):
                try:
                    doc_target.removeObject(o.Name)
                except Exception:
                    pass
            for o in list(doc_target.Objects):
                if o not in keep_objs:
                    try:
                        doc_target.removeObject(o.Name)
                    except Exception:
                        pass
                        
            # If this is not the reference document, link VarSet from master reference document
            if not is_ref and os.path.exists(ref_file_path):
                doc_ref = get_ext_doc(ref_filename)
                if doc_ref:
                    master_vs = doc_ref.getObject("VarSet")
                    if master_vs:
                        link_vs = doc_target.addObject("App::Link", "VarSet")
                        link_vs.setLink(master_vs)
                    
            doc_target.recompute()
            doc_target.save()
            
            created_files.append(target_path)
            
            # Close all open docs cleanly
            for d in list(FreeCAD.listDocuments().values()):
                try:
                    FreeCAD.closeDocument(d.Name)
                except Exception:
                    pass
            open_ext_docs.clear()
            import gc
            gc.collect()
                
    # 3. Create Top-Level Assembly Document
    asm_path = os.path.join(output_dir, assembly_filename)
    print(f"\n[Assembly] Creating Top-Level Assembly: {assembly_filename}...")
    if os.path.exists(asm_path):
        os.remove(asm_path)
    doc_asm = FreeCAD.newDocument("CarAssembly")
    doc_asm.saveAs(asm_path)
    
    open_asm_docs = []
    for comp_path in created_files:
        if comp_path == asm_path or not os.path.exists(comp_path):
            continue
        try:
            comp_doc = FreeCAD.openDocument(comp_path)
            open_asm_docs.append(comp_doc)
            for rp in get_root_parts(comp_doc):
                link = doc_asm.addObject("App::Link", f"Link_{rp.Label}")
                link.setLink(rp)
                link.Label = rp.Label
            for rb in [o for o in comp_doc.RootObjects if o.isDerivedFrom("PartDesign::Body")]:
                link = doc_asm.addObject("App::Link", f"Link_{rb.Label}")
                link.setLink(rb)
                link.Label = rb.Label
        except Exception as e:
            print(f"  [Warning] Could not link {os.path.basename(comp_path)} to assembly: {e}")
        
    doc_asm.recompute()
    doc_asm.save()
    
    for d in list(FreeCAD.listDocuments().values()):
        try:
            FreeCAD.closeDocument(d.Name)
        except Exception:
            pass
    
    print("\n" + "=" * 76)
    print("MIGRATION COMPLETED SUCCESSFULLY!")
    print(f"Generated Files: {len(created_files)} component files + {assembly_filename}")
    print(f"Output Location: {output_dir}")
    print("=" * 76 + "\n")


def cmd_inspect(args):
    """Inspects an object or document's full dependency tree."""
    source_file = args.source or DEFAULT_SOURCE_FILE
    doc = FreeCAD.openDocument(source_file)
    graph = analyze_document_graph(doc)
    
    obj_name = args.object
    if not obj_name:
        print(f"\nDocument Summary: {source_file}")
        print(f"Total Objects : {len(doc.Objects)}")
        print(f"Root Parts    : {[doc.getObject(p).Label for p in graph['root_parts']]}")
        print(f"Root Bodies   : {[doc.getObject(b).Label for b in graph['root_bodies']]}")
        print(f"VarSets       : {graph['varsets']}")
        FreeCAD.closeDocument(doc.Name)
        return
        
    obj = doc.getObject(obj_name)
    if not obj:
        # Try search by label
        matches = [o for o in doc.Objects if o.Label.lower() == obj_name.lower()]
        if matches:
            obj = matches[0]
            obj_name = obj.Name
        else:
            print(f"Object '{obj_name}' not found in {source_file}.")
            FreeCAD.closeDocument(doc.Name)
            return
            
    print("\n" + "=" * 70)
    print(f"INSPECT OBJECT: '{obj.Label}' ({obj.Name}) [{obj.TypeId}]")
    print("=" * 70)
    
    deps = graph["dependencies"].get(obj.Name, set())
    rev_deps = graph["reverse_deps"].get(obj.Name, set())
    
    print(f"Dependencies (Depends on {len(deps)} objects):")
    for d in sorted(deps):
        d_obj = doc.getObject(d)
        lbl = d_obj.Label if d_obj else "unknown"
        print(f"   -> {lbl} ({d})")
        
    print(f"\nDependents (Used by {len(rev_deps)} objects):")
    for r in sorted(rev_deps):
        r_obj = doc.getObject(r)
        lbl = r_obj.Label if r_obj else "unknown"
        print(f"   <- {lbl} ({r})")
        
    if hasattr(obj, "Group"):
        print(f"\nGroup Children ({len(obj.Group)}): {[c.Label + ' (' + c.Name + ')' for c in obj.Group]}")
        
    print("=" * 70 + "\n")
    FreeCAD.closeDocument(doc.Name)


def cmd_plan(args):
    """Generates the migration plan JSON file."""
    source_file = args.source or DEFAULT_SOURCE_FILE
    output_dir = args.outdir or DEFAULT_OUTPUT_DIR
    plan_file = args.plan or DEFAULT_PLAN_FILE
    
    custom_map = {}
    if args.move:
        # e.g. --move Body048:13_ThreadedRearAxle.FCStd
        for m in args.move:
            if ":" in m:
                obj_id, target_f = m.split(":", 1)
                custom_map[obj_id.strip()] = target_f.strip()
                
    plan = generate_migration_plan(source_file, output_dir, custom_mappings=custom_map)
    
    with open(plan_file, "w", encoding="utf-8") as f:
        json.dump(plan, f, indent=2, ensure_ascii=False)
        
    print(f"\n[Plan] Migration plan written to: {plan_file}")
    print(f"       Total Destination Files: {len(plan['destinations'])}")
    print(f"       Execution Tiers        : {len(plan['execution_tiers'])}")
    print("\nYou can edit 'migration_plan.json' to customize destinations, then run:")
    print(f"  python freecad_migrator.py migrate --plan {plan_file}\n")


def cmd_migrate(args):
    """Applies and executes the migration plan."""
    plan_file = args.plan or DEFAULT_PLAN_FILE
    if not os.path.exists(plan_file):
        print(f"Error: Migration plan '{plan_file}' not found. Run 'plan' command first.")
        sys.exit(1)
    execute_migration_plan(plan_file)


def main():
    parser = argparse.ArgumentParser(
        description="FreeCAD Generic Modularization & Migration Framework",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to execute")
    
    # Subcommand: inspect
    p_insp = subparsers.add_parser("inspect", help="Inspect object dependencies and relationships")
    p_insp.add_argument("--source", default=DEFAULT_SOURCE_FILE, help="Source .FCStd file")
    p_insp.add_argument("--object", help="Object name or label to inspect")
    
    # Subcommand: plan
    p_plan = subparsers.add_parser("plan", help="Generate a customizable migration plan JSON")
    p_plan.add_argument("--source", default=DEFAULT_SOURCE_FILE, help="Source .FCStd file")
    p_plan.add_argument("--outdir", default=DEFAULT_OUTPUT_DIR, help="Destination directory for modular files")
    p_plan.add_argument("--plan", default=DEFAULT_PLAN_FILE, help="Output plan JSON filepath")
    p_plan.add_argument("--move", action="append", help="Custom move override in format '<ObjectName>:<TargetFile.FCStd>'")
    
    # Subcommand: migrate
    p_mig = subparsers.add_parser("migrate", help="Execute the migration plan")
    p_mig.add_argument("--plan", default=DEFAULT_PLAN_FILE, help="Path to migration_plan.json")
    
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(0)
        
    if args.command == "inspect":
        cmd_inspect(args)
    elif args.command == "plan":
        cmd_plan(args)
    elif args.command == "migrate":
        cmd_migrate(args)


if __name__ == "__main__":
    main()
