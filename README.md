# pq2-font-patch

#### A mod that allows for a 2x upscaled font to work in Persona Q2 (USA)

This mod works by replacing the game's BCFNT font with one of a higher resolution and scaling it down to original size through a code patch.
* Only the USA version of PQ2 is supported `TitleID: 00040000001D7100`

## Installation

#### Aemulus Mod
* Download the latest ZIP file for the Upscaled Font mod from Releases
* Place the contents from the ZIP into your Aemulus packages folder for Persona Q2 and build mod.cpk

#### Code Patch
* Download the latest Font Patch ZIP file that you prefer
  * Font_Patch_ModCpk - Only has mod.cpk support enabled + Font Patch
  * Font_Patch_Full - Contains all code patches from [PQ_Patches](https://github.com/DeathChaos25/PQ_Patches) + Font Patch
* On Citra or Azahar click on "Open Mods Location" for PQ2
* Place the files from the ZIP you chose into these paths:
  * load/mods/00040000001D7100/exheader.bin
  * load/mods/00040000001D7100/exefs/code.bin

## Patching

In case you prefer patching code.bin yourself, you can use the font_patch.py script.

  #### Requirements
  * Python3
  * An already patched code.bin file that enables (at least) mod.cpk support from [PQ_Patches](https://github.com/DeathChaos25/PQ_Patches).

  #### Usage
  * Place your code.bin file next to font_patch.py
  * Open command prompt in that directory and run
    ```txt
    python3 font_patch.py code.bin
    ```
  * Place the new code_patched.bin in the exefs folder and rename it to replace the previous one
    
