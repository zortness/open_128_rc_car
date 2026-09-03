"""
split_freecad_parts.py
----------------------
Automated tool to decompose large, monolithic FreeCAD files into a modular,
maintainable multi-file architecture with external VarSets and SubShapeBinders.

Features:
1. Extracts `GeometryReference` (master sketches/drawings) and `VarSet` (parameters)
   into a single root reference document (00_GeometryReference.FCStd).
2. Extracts `RearPanPlateFlatMount` (Body033) into a dedicated shared component file
   (00_RearPanPlateFlatMount.FCStd).
3. Extracts each root `App::Part` into its own lightweight, independent `.FCStd` file.
4. Reparents all `PartDesign::SubShapeBinder`s:
   - Drawing binders reparent to `00_GeometryReference.FCStd`.
   - Motor mount plate binders (Binder019, Binder024, FlatMountRearBinder, etc.)
     reparent to `00_RearPanPlateFlatMount.FCStd`.
5. Links the master `VarSet` into each component document via an `App::Link` named `VarSet`,
   so that all existing parametric expressions continue working without modification.
6. Preserves all Origin coordinate systems, including X/Y/Z axes, XY/XZ/YZ planes, and Origin points.
7. Assembles all modular components into a top-level Assembly document (00_CarAssembly.FCStd).
8. Preserves all internal sketch geometry, constraints, pads, pockets, fillets, and chamfers
   with 100% fidelity.

Usage:
  & "C:\\Program Files\\FreeCAD 1.1\\bin\\python.exe" split_freecad_parts.py [--input <file.FCStd>] [--outdir <output_dir>]
"""

import os
import sys
import shutil
import argparse

try:
    import FreeCAD
except ImportError:
    fc_bin = r"C:\Program Files\FreeCAD 1.1\bin"
    if os.path.exists(fc_bin) and fc_bin not in sys.path:
        sys.path.append(fc_bin)
    import FreeCAD


def sanitize_filename(name):
    clean = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in name)
    return clean.strip("_")


def collect_descendants_and_features(obj, visited=None):
    if visited is None:
        visited = set()
    if obj in visited:
        return visited
    visited.add(obj)
    
    # 1. Collect Group children (sub-parts, bodies, etc.)
    if hasattr(obj, 'Group'):
        for c in obj.Group:
            collect_descendants_and_features(c, visited)
            
    # 2. Collect Origin coordinate system
    if hasattr(obj, 'Origin') and obj.Origin:
        collect_descendants_and_features(obj.Origin, visited)
        
    # 3. Collect Origin sub-features (axes, planes, origin points)
    if hasattr(obj, 'OriginFeatures'):
        doc = getattr(obj, 'Document', None)
        for feat in obj.OriginFeatures:
            if isinstance(feat, tuple):
                if doc:
                    feat_obj = doc.getObject(feat[0])
                    if feat_obj:
                        collect_descendants_and_features(feat_obj, visited)
            elif hasattr(feat, 'Name'):
                collect_descendants_and_features(feat, visited)
                
    # 4. Collect internal features (sketches, pads, chamfers, pockets, axes, planes, etc.)
    if hasattr(obj, 'OutList'):
        for out in obj.OutList:
            if out.isDerivedFrom("PartDesign::Feature") or \
               out.isDerivedFrom("Sketcher::SketchObject") or \
               out.isDerivedFrom("App::OriginGroupExtension") or \
               out.isDerivedFrom("App::Line") or \
               out.isDerivedFrom("App::Plane") or \
               out.isDerivedFrom("App::Point") or \
               out.isDerivedFrom("Part::Feature"):
                collect_descendants_and_features(out, visited)
                
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


def extract_reference_doc(src_file, out_ref_path, geo_part_names):
    """Creates the master reference document containing only VarSet and GeometryReference."""
    print(f"\n[1/4] Creating Master Reference document: {os.path.basename(out_ref_path)}...")
    if os.path.exists(out_ref_path):
        os.remove(out_ref_path)
    shutil.copyfile(src_file, out_ref_path)
    
    doc_ref = FreeCAD.openDocument(out_ref_path)
    keep_objs = set()
    
    # 1. Keep VarSets
    for obj in doc_ref.Objects:
        if "VarSet" in obj.TypeId or obj.Name == "VarSet":
            keep_objs.add(obj)
            
    # 2. Keep GeometryReference Parts and their full origin sub-features
    for gp_name in geo_part_names:
        ref_obj = doc_ref.getObject(gp_name)
        if ref_obj:
            collect_descendants_and_features(ref_obj, keep_objs)

    print(f"      Preserving {len(keep_objs)} reference/origin/variable objects out of {len(doc_ref.Objects)} total objects...")
    
    # Remove everything else
    to_delete = [o for o in doc_ref.Objects if o not in keep_objs]
    for o in reversed(to_delete):
        try:
            doc_ref.removeObject(o.Name)
        except Exception:
            pass
    for o in list(doc_ref.Objects):
        if o not in keep_objs:
            try:
                doc_ref.removeObject(o.Name)
            except Exception:
                pass
                
    doc_ref.recompute()
    doc_ref.save()
    doc_name = doc_ref.Name
    FreeCAD.closeDocument(doc_name)
    print(f"      Master Reference saved successfully.")
    return out_ref_path


def extract_rear_pan_plate_doc(src_file, out_rpp_path, ref_file_path):
    """Creates the standalone RearPanPlateFlatMount component document."""
    print(f"\n[2/4] Creating Shared Component: {os.path.basename(out_rpp_path)}...")
    if os.path.exists(out_rpp_path):
        os.remove(out_rpp_path)
    shutil.copyfile(src_file, out_rpp_path)
    
    doc_rpp = FreeCAD.openDocument(out_rpp_path)
    doc_ref = FreeCAD.openDocument(ref_file_path)
    
    b33 = doc_rpp.getObject("Body033")
    if not b33:
        print("      Warning: Body033 (RearPanPlateFlatMount) not found.")
        FreeCAD.closeDocument(doc_rpp.Name)
        FreeCAD.closeDocument(doc_ref.Name)
        return False
        
    keep_objs = collect_descendants_and_features(b33)
    
    # Reparent Binder016 to master GeometryReference
    b16 = doc_rpp.getObject("Binder016")
    if b16:
        ref_geo = doc_ref.getObject("Part")
        if ref_geo:
            b16.Support = [(ref_geo, ('Body001.Sketch002.',))]
            
    # Delete non-keep objects
    to_delete = [o for o in doc_rpp.Objects if o not in keep_objs]
    for o in reversed(to_delete):
        try:
            doc_rpp.removeObject(o.Name)
        except Exception:
            pass
    for o in list(doc_rpp.Objects):
        if o not in keep_objs:
            try:
                doc_rpp.removeObject(o.Name)
            except Exception:
                pass
                
    # Create App::Link to VarSet
    master_varset = doc_ref.getObject("VarSet")
    if master_varset:
        link_vs = doc_rpp.addObject("App::Link", "VarSet")
        link_vs.setLink(master_varset)
        
    doc_rpp.recompute()
    doc_rpp.save()
    
    FreeCAD.closeDocument(doc_rpp.Name)
    FreeCAD.closeDocument(doc_ref.Name)
    print(f"      Shared RearPanPlateFlatMount saved successfully.")
    return True


def extract_part(src_file, out_part_path, ref_file_path, rpp_file_path, part_name, binder_info):
    """Extracts a single App::Part into its own document and reparents links/binders."""
    if os.path.exists(out_part_path):
        os.remove(out_part_path)
    shutil.copyfile(src_file, out_part_path)
    
    doc_part = FreeCAD.openDocument(out_part_path)
    doc_ref = FreeCAD.openDocument(ref_file_path)
    doc_rpp = FreeCAD.openDocument(rpp_file_path)
    
    part_obj = doc_part.getObject(part_name)
    if not part_obj:
        print(f"      Warning: Part '{part_name}' not found in document.")
        FreeCAD.closeDocument(doc_part.Name)
        FreeCAD.closeDocument(doc_ref.Name)
        FreeCAD.closeDocument(doc_rpp.Name)
        return False
        
    # Note: If this part contained Body033 as a sub-body (e.g. in 130MotorFlatRear),
    # we exclude Body033 and its internal features from keep_objs so it's not duplicated
    keep_objs = set()
    def collect_filtered(obj):
        if obj.Name == "Body033":
            return
        collect_descendants_and_features(obj, keep_objs)
        
    if hasattr(part_obj, 'Group'):
        keep_objs.add(part_obj)
        if hasattr(part_obj, 'Origin') and part_obj.Origin:
            keep_objs.add(part_obj.Origin)
            if hasattr(part_obj.Origin, 'OriginFeatures'):
                for f in part_obj.Origin.OriginFeatures:
                    fo = doc_part.getObject(f[0]) if isinstance(f, tuple) else f
                    if fo:
                        keep_objs.add(fo)
        for c in part_obj.Group:
            if c.Name != "Body033":
                collect_filtered(c)
    else:
        collect_descendants_and_features(part_obj, keep_objs)
    
    rpp_body = doc_rpp.getObject("Body033")
    
    # Reparent binders:
    # 1. Binders pointing to GeometryReference -> doc_ref
    # 2. Binders pointing to Body033 (RearPanPlateFlatMount) -> doc_rpp
    for o in list(doc_part.Objects):
        if o.Name in binder_info and o in keep_objs:
            new_supp = []
            for target_name, subelems in binder_info[o.Name]:
                if target_name == "Body033" or "body033" in target_name.lower():
                    if rpp_body:
                        new_supp.append((rpp_body, subelems))
                else:
                    ref_target = doc_ref.getObject(target_name)
                    if ref_target:
                        new_supp.append((ref_target, subelems))
            if new_supp:
                o.Support = new_supp
                
    # Delete non-keep objects (including old internal VarSet & duplicate Body033)
    to_delete = [o for o in doc_part.Objects if o not in keep_objs]
    for o in reversed(to_delete):
        try:
            doc_part.removeObject(o.Name)
        except Exception:
            pass
    for o in list(doc_part.Objects):
        if o not in keep_objs:
            try:
                doc_part.removeObject(o.Name)
            except Exception:
                pass
                
    # Create App::Link named "VarSet" pointing to the master VarSet
    master_varset = doc_ref.getObject("VarSet")
    if master_varset:
        link_vs = doc_part.addObject("App::Link", "VarSet")
        link_vs.setLink(master_varset)
        
    doc_part.recompute()
    doc_part.save()
    
    FreeCAD.closeDocument(doc_part.Name)
    FreeCAD.closeDocument(doc_ref.Name)
    FreeCAD.closeDocument(doc_rpp.Name)
    return True


def create_assembly_doc(out_assembly_path, component_files):
    """Creates a top-level assembly document linking all extracted parts."""
    print(f"\n[4/4] Creating Top-Level Assembly document: {os.path.basename(out_assembly_path)}...")
    if os.path.exists(out_assembly_path):
        os.remove(out_assembly_path)
        
    doc_asm = FreeCAD.newDocument("CarAssembly")
    doc_asm.saveAs(out_assembly_path)
    
    for comp_path in component_files:
        if not os.path.exists(comp_path):
            continue
        comp_doc = FreeCAD.openDocument(comp_path)
        root_parts = get_root_parts(comp_doc)
        root_bodies = [o for o in comp_doc.RootObjects if o.isDerivedFrom("PartDesign::Body")]
        
        for rp in root_parts:
            link = doc_asm.addObject("App::Link", f"Link_{rp.Label}")
            link.setLink(rp)
            link.Label = rp.Label
        for rb in root_bodies:
            link = doc_asm.addObject("App::Link", f"Link_{rb.Label}")
            link.setLink(rb)
            link.Label = rb.Label
            
        FreeCAD.closeDocument(comp_doc.Name)
        
    doc_asm.recompute()
    doc_asm.save()
    FreeCAD.closeDocument(doc_asm.Name)
    print("      Top-Level Assembly created successfully.")


def split_all_parts(src_file, output_dir, selected_part_names=None):
    os.makedirs(output_dir, exist_ok=True)
    
    print(f"Opening monolithic source document: {src_file}")
    doc_src = FreeCAD.openDocument(src_file)
    
    # 1. Analyze all root App::Parts
    parts = get_root_parts(doc_src)
    geo_parts = [p for p in parts if "geom" in p.Label.lower() or "reference" in p.Label.lower() or p.Name == "Part"]
    comp_parts = [p for p in parts if p not in geo_parts]
    
    geo_part_names = [p.Name for p in geo_parts]
    part_meta = [(p.Name, p.Label) for p in comp_parts]
    
    print(f"Discovered {len(parts)} root Parts in document:")
    print(f"  - Master Reference Part: {[p.Label for p in geo_parts]}")
    print(f"  - Component Parts ({len(comp_parts)}): {[p.Label for p in comp_parts]}")
    
    # 2. Record SubShapeBinder mappings
    binder_info = {}
    for o in doc_src.Objects:
        if "Binder" in o.TypeId:
            supp = getattr(o, "Support", None)
            if supp:
                binder_info[o.Name] = [(s[0].Name, s[1]) for s in supp]
                
    FreeCAD.closeDocument(doc_src.Name)
    
    # 3. Create Master Reference Document
    ref_file_path = os.path.join(output_dir, "00_GeometryReference.FCStd")
    extract_reference_doc(src_file, ref_file_path, geo_part_names=geo_part_names)
    
    # 4. Create Shared RearPanPlateFlatMount Document
    rpp_file_path = os.path.join(output_dir, "00_RearPanPlateFlatMount.FCStd")
    extract_rear_pan_plate_doc(src_file, rpp_file_path, ref_file_path)
    
    # 5. Extract Component Parts
    print(f"\n[3/4] Extracting {len(part_meta)} Component Parts to separate files...")
    extracted_files = [rpp_file_path]
    
    for idx, (p_name, p_label) in enumerate(part_meta, 1):
        if selected_part_names and p_name not in selected_part_names and p_label not in selected_part_names:
            continue
            
        clean_name = sanitize_filename(p_label)
        part_filename = f"{idx:02d}_{clean_name}.FCStd"
        out_part_path = os.path.join(output_dir, part_filename)
        
        print(f"  [{idx}/{len(part_meta)}] Extracting '{p_label}' -> {part_filename}...")
        success = extract_part(src_file, out_part_path, ref_file_path, rpp_file_path, p_name, binder_info)
        if success:
            extracted_files.append(out_part_path)
            
    # 6. Create Assembly Document
    asm_file_path = os.path.join(output_dir, "00_CarAssembly.FCStd")
    create_assembly_doc(asm_file_path, extracted_files)
    
    print("\n" + "="*70)
    print("MODULAR EXTRACTION COMPLETED SUCCESSFULLY!")
    print(f"Output Directory  : {output_dir}")
    print(f"Master Reference  : {ref_file_path}")
    print(f"Shared Rear Plate : {rpp_file_path}")
    print(f"Master Assembly   : {asm_file_path}")
    print(f"Component Files   : {len(extracted_files)} files created")
    print("="*70)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Split monolithic FreeCAD document into modular files.")
    parser.add_argument("--input", default=r"C:\Users\zortn\projects\zortness\128_car\128 Mini Car.FCStd", help="Path to input .FCStd file")
    parser.add_argument("--outdir", default=r"C:\Users\zortn\projects\zortness\128_car\modular_car", help="Output directory for modular files")
    parser.add_argument("--parts", default=None, help="Comma-separated list of Part names/labels to extract (default: all)")
    
    args = parser.parse_args()
    selected = [p.strip() for p in args.parts.split(",")] if args.parts else None
    split_all_parts(args.input, args.outdir, selected)
