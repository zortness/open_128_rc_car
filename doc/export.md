# Exporting Files #

## Requirements ##
You will need FreeCAD 1.1 or higher installed.
You will need Python 3.14 or higher installed.

## Running The Exports ##
To run the default export configuration:

```bash
cd tools
python freecad_exporter.py export
```

This will generate a `dist` folder with all of the default STL files based
on the configuration included. 

To export parametric variants (special cases):

```bash
cd tools
python export_parametric_variants.py 
```

## Possible Issues ##
The python tools were written on a Windows machine with FreeCAD installed in the 
default directory, and will make some assumptions along those lines. If that is not
your situation, you may need to modify the Python files. I will probably eventually
fix these to be more flexible.


## Advanced Configuration ##

### FreeCAD Exporter ###
You can modify which files are exported from the FCStd files and how they are
named by modifying the `export_config.json` file. This file can also be edited
by the `freecad_exporter.py` program in `config` mode. 

```
[Scan] Scanning 23 .FCStd files in '..\modular_car'...

================================================================================================
INTERACTIVE EXPORT CONFIGURATION
================================================================================================
#    Status   Format  File                         Label                      Output File
------------------------------------------------------------------------------------------------
1    [x] ON   STL     01_PanChassisTwoPiece.FCSt   TwoPiecePanMainPlate       PanChassisTwoPieceMain.stl
2    [x] ON   STL     01_PanChassisTwoPiece.FCSt   RightSteeringKnuckle       SteeringKnuckle_Right.stl
3    [x] ON   STL     01_PanChassisTwoPiece.FCSt   SteeringLink               SteeringLink.stl
4    [x] ON   STL     01_PanChassisTwoPiece.FCSt   LeftSteeringKnuckle        SteeringKnuckle_Left.stl
5    [ ] OFF  STL     02_WheelProxies.FCStd        RightRear                  RightRear.stl
6    [ ] OFF  STL     02_WheelProxies.FCStd        RightFront                 RightFront.stl
...
```

Running in config mode will list all possible bodies in the FCStd files of the
repo and give you the option to select which ones should be included in the
export process. 

### Exporting Parametric Parts ###
The `export_parametric_variants.py` program will default to exporting the 
1410 motor mounts with various values. It does have a variety of input options:

```
usage: export_parametric_variants.py [-h] [--file FILE] [--varset VARSET] [--param PARAM] [--increments INCREMENTS] [--body BODY]
                                     [--format {stl,step,3mf,obj}] [--outdir OUTDIR] [--suffix-template SUFFIX_TEMPLATE]
                                     [--linear-deflection LINEAR_DEFLECTION] [--angular-deflection ANGULAR_DEFLECTION]

FreeCAD Parametric Variant Exporter

options:
  -h, --help            show this help message and exit
  --file FILE           Path to .FCStd file (default: modular_car/15_1410MotorFlatRear.FCStd)
  --varset VARSET       Name of VarSet object (default: auto-detect '1410MotorMountVars')
  --param PARAM         Target parameter name (default: MountOffset)
  --increments INCREMENTS
                        Comma-separated list of increment values (overrides VarSet array)
  --body BODY           Name/Label of PartDesign::Body to export (default: auto-detect)
  --format {stl,step,3mf,obj}
                        Export format (default: stl)
  --outdir OUTDIR       Output directory (default: dist)
  --suffix-template SUFFIX_TEMPLATE
                        Suffix template (default: '_offset_{val}mm')
  --linear-deflection LINEAR_DEFLECTION
                        Linear deflection for mesh export (default: 0.01)
  --angular-deflection ANGULAR_DEFLECTION
                        Angular deflection for mesh export (default: ~10 deg)
```

The parametric export is useful for parts that may need several different sizes based on
an input variable.
