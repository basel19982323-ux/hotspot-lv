Hotspot LV {version} for Windows (64-bit)
==========================================

An open screening tool for secondary-substation (nätstation) transformers under electrification: your own station
data, a whole fleet, or a synthetic street, judged with the IEC 60076-7 thermal model against IEC 60076-7 and EIFS
2023:3 limits. Simulation with generic constants — results are indicative, every limit is stated in the program.

Start
-----
1. Unzip the whole HotspotLV folder to a short path, for example C:\Tools\HotspotLV (a very deep folder — more than
   about 140 characters — can stop the program from starting). Keep the folder together: HotspotLV.exe needs the
   _internal folder next to it.
2. Double-click HotspotLV.exe. The window opens in a few seconds.

Windows SmartScreen
-------------------
The program is not code-signed. On the first start Windows may say "Windows protected your PC — Unknown publisher".
Choose "More info" and then "Run anyway" if you trust the download. Check the download first: in PowerShell,

    Get-FileHash .\HotspotLV-{version}-win64.zip -Algorithm SHA256

must print the same value as the .sha256 file published with the release. Managed PCs (AppLocker, Smart App
Control) may block unsigned programs entirely; ask your IT department.

Internet and files
------------------
- Works offline with what it ships: the Västerås / Eskilstuna A weather of 2023 and three synthetic example stations.
- Another SMHI weather station, or station data outside 2023, downloads that station's hourly temperature archive
  once from opendata.smhi.se (about 7 MB) into %USERPROFILE%\.hotspotlv\raw. Nothing else is sent or fetched.
- Station cards (PDF) and fleet tables (CSV) are saved where you choose.
- To remove the program: delete the HotspotLV folder and, if it exists, %USERPROFILE%\.hotspotlv.

Licences
--------
- Hotspot LV: MIT licence, Copyright (c) 2026 Basel Haijar — LICENSE.txt.
- The bundled data keep their own licences (SMHI and Norwegian household data: CC BY 4.0; PVGIS: JRC terms) —
  listed in the program under "Assumptions & sources" and in THIRD-PARTY-NOTICES.txt.
- The program includes Python, Qt for Python / Qt 6 (LGPL-3.0, texts in the LICENSES folder) and other libraries,
  each under its own licence, with source links — THIRD-PARTY-NOTICES.txt.

Source code and issues: https://github.com/basel19982323-ux/hotspot-lv

Check an installation
---------------------
    HotspotLV.exe --self-test C:\Temp\hotspot-check

runs the program's own end-to-end check without opening a window (station, PDF, fleet, scenario runs, cancel, close)
and writes C:\Temp\hotspot-check\self-test.txt; the exit code is 0 when every step passed.
