# Getting Started #

This guide will walk you through printing and assembling the most basic car.

## Hardware ##
See the [Materials](materials.md) documentation for specific pieces. You will
need:
- M2 Screws and nuts (See [Materials](materials.md))
- Some M3 Screws and nuts (See [Materials](materials.md))
- M3 Threaded rod, cut down between 70-75mm (See [Materials](materials.md), [Assembly](assembly.md))
- A 130-size DC motor (See [Electronics](electronics.md))
- A Brushed motor Electronic Speed Controller (ESC) (See [Electronics](electronics.md))
- A battery configuration (See [Batteries](batteries.md))
 - 2x LiFePO4 14500 size (AA) with charger
  - Battery terminal connectors
 - OR a 2S LiPo / LiIon battery
  - Appropriate connectors (XT-30 or JST)
 - An appropriate charge for either setup
- A Transmitter and Receiver (See [Transmitters and Receivers](receivers.md))
- 2x narrow front tires
- 2x wide rear tires

## Print ##
Download the latest release.
Print these parts, see [Printing](printing.md) for additional information for 
specific parts.

- 1 piece chassis `PanChassisSinglePieceMain.stl`
 - or With AA mounts if using 14500 or AA size batteries `PanChassisSinglePieceAAMain.stl`
- Steering knuckles (see [Printing](printing.md))
 - `SteeringKnuckle_Right.stl` and `SteeringKnuckle_Left.stl`
- Steering linkage `SteeringLink.stl`
- Servo Mount
 - Start with `MicroServoMountLongThrow.stl`
 - Optionally, print `MicroServoHorn5mmX9mm.stl`, but it is recommended to trim down the horns that came with your servos
- Motor mount `130MotorTiltedRearMount.stl` (see [Printing](printing.md))
- 10-tooth Pinion Gear `Pinion_10T_M05.stl`
- 42-tooth Straight Main Gear `MainGear42T_M05.stl`
- Rear Axle Mount `ThreadedRearAxleMountComnbined.stl`
- Rear Axle Spacer for left side `ThreadedRearAxleSpacer.stl`
- Rear Axle Gear Mount for right side `ThreadedSolidAxleGearMount.stl`
- 2x Front Wheel `FrontWheel.stl`
- 2x Rear Wheel with no bearing `RearWheel.stl`
- 2x M2 nut `M2PlasticWheelNut4.5mm.stl` (probably print more)
- 4x M3 nut `M3PlasticWheelNut5.5mm.stl` (probably print more)


Optionally, print some basic tools
- Pinion Puller tool `PinionPuller.stl`
- Wheel Nut Driver 4.5mm `WheelNutDriver4.5mm.stl`
- Wheel Nut Driver 5.5mm `WheelNutDriver5.5mm.stl`

## Assembly ##
See [Assembly](assembly.md).
