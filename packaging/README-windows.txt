Hotspot LV {version} for Windows (64-bit)
==========================================

An open screening tool for secondary-substation (nätstation) transformers under electrification: your own station
data, a whole fleet, or a synthetic street, judged with the IEC 60076-7 thermal model against IEC 60076-7 and EIFS
2023:3 limits. Simulation with generic constants — results are indicative, every limit is stated in the program.

Check the download
------------------
Before you unzip, check that the zip is the published file. In PowerShell, in the folder of the download:

    (Get-FileHash .\HotspotLV-{version}-win64.zip -Algorithm SHA256).Hash -eq (Get-Content .\HotspotLV-{version}-win64.zip.sha256).Split(' ')[0]

must print True (the .sha256 file is published with the release).

Start
-----
1. Unzip the whole HotspotLV folder to a short path, for example C:\Tools\HotspotLV (a very deep folder — more than
   about 140 characters — stops the program from starting). Keep the folder together: HotspotLV.exe needs the
   _internal folder next to it.
2. Double-click HotspotLV.exe. The window opens in a few seconds.

Windows SmartScreen
-------------------
The program is not code-signed. On the first start Windows may say "Windows protected your PC — Unknown publisher".
Choose "More info" and then "Run anyway" if you trust the download (check it first, see above). Managed PCs
(AppLocker, Smart App Control) may block unsigned programs entirely; ask your IT department.

Internet and files
------------------
- Works offline with what it ships: the Västerås / Eskilstuna A weather of 2023 and three synthetic example stations.
- Station data outside 2023, or another SMHI weather station, needs that station's hourly temperatures from SMHI:
  the quality-checked archive (about 7 MB) and, for the most recent months, SMHI's latest-months data (about
  0.1 MB). They are downloaded over HTTPS from opendata-download-metobs.smhi.se into %USERPROFILE%\.hotspotlv\raw
  and used again from there; a newer copy is fetched only when the data reach past the saved one. SMHI publishes
  its checked archive about three months late; hours after it come from the latest months and the program says so
  in the result. Nothing else is sent or fetched.
- Station cards (PDF) and fleet tables (CSV) are saved where you choose.
- To remove the program: delete the HotspotLV folder and, if it exists, %USERPROFILE%\.hotspotlv.

Licences
--------
- Hotspot LV: MIT licence, Copyright (c) 2026 Basel Haijar — LICENSE.txt.
- The bundled data keep their own licences (SMHI temperatures, the Norwegian household data and the EV charging
  data: CC BY 4.0; PVGIS: JRC terms) — listed in the program under "Assumptions & sources" and in
  THIRD-PARTY-NOTICES.txt.
- The program includes Python, Qt for Python / Qt 6 (LGPL-3.0, texts in the LICENSES folder) and other libraries,
  each under its own licence, with source links — THIRD-PARTY-NOTICES.txt.
- The Microsoft Visual C++ runtime files in _internal (vcruntime140*.dll, msvcp140*.dll) are Microsoft's; they may
  not be modified, and are provided only to run this program.

Source code and issues: {url}
This build: version {version}, commit {commit}.

Check an installation
---------------------
The program can check itself without opening a window (station, PDF, fleet, scenario runs, cancel, close). In
PowerShell:

    Start-Process .\HotspotLV.exe -ArgumentList '--self-test', 'C:\Temp\hotspot-check' -Wait

takes about two minutes. Then open C:\Temp\hotspot-check\self-test.txt: its last line says ALL PASSED when every
step passed. (A plain .\HotspotLV.exe --self-test ... returns at once, because the program has no console; the
check still runs.)
