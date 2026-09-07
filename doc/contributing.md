# Contributing #

## File Organization ##
The file organization is still developing at this point. Originally all Parts were
contained within one file. We have two options:
- Contain variants within the same FCStd file as the parent object
- Split variants off into their own FCStd file that can import geometry from the original

There are some parts within this project that still follow the first option, but
the second option is emerging as the recommended method.

If you wish to make a variant or new part, start a new FCStd file and import the 
geometry from any parts that the new part depends on.

## Part Naming ##
[Provisional]
- You do not need to include a numeric prefix. The existing numbers are an artifact of splitting
the original file.
- You can create subfolders if there are multiple related files.

## Pull Requests ##
### Parts and Modifications ###
Use the History Workbench to generate a visual and/or property level diff if you are
modifying an existing Part. Take a screenshot of the diff screen to include in your
pull request.

If you are creating a new part, include a screenshot of the new part.

### Documentation ###
For documentation, you can make a pull request without the need of any additional information.
