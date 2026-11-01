{test_note}Hotspot LV {version} for Windows (64-bit)
==========================================

An open screening tool for secondary-substation (nätstation) transformers under electrification: your own station
data, a whole fleet, or a synthetic street, judged with the IEC 60076-7 thermal model against IEC 60076-7 and EIFS
2023:3 limits. Simulation with generic constants — results are indicative, every limit is stated in the program.

Check the download
------------------
Before you unzip, check that the zip is the published file. In PowerShell, in the folder of the download:

    (Get-FileHash .\{zip} -Algorithm SHA256).Hash -eq (Get-Content .\{zip}.sha256).Split(' ')[0]

must print True (the .sha256 file is published with the release).

Start
-----
1. Unzip the whole HotspotLV folder to a short path, for example C:\Tools\HotspotLV (a very deep folder — more than
   about 140 characters — stops the unzip or the program). Keep the folder together: HotspotLV.exe needs the
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
  and used again from there. A newer copy is fetched only when the data need hours the saved one does not have:
  the latest months at most once per run, the archive only to close a gap and at most once a day. SMHI publishes
  its checked archive about three months late; hours after it come from the latest months, and the result's notes
  say so. Nothing else is sent or fetched.
- Station cards (PDF) and fleet tables (CSV) are saved where you choose. Qt, the program's window toolkit, remembers
  the last folder of its file dialogs in the registry (HKEY_CURRENT_USER\Software\QtProject, shared with other
  programs built with Qt).
- To remove the program: delete the HotspotLV folder and, if it exists, %USERPROFILE%\.hotspotlv. The QtProject
  registry entry may stay; delete it only if no other Qt program uses it.

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

One station's report without the window
---------------------------------------
HotspotLV.exe can write one station's report (PDF or HTML) without opening its window. In PowerShell, in the
HotspotLV folder:

    $p = Start-Process .\HotspotLV.exe -ArgumentList 'report', '"C:\Data\station.csv"', '--kva', '500', '--out', '"C:\Data\station-report.pdf"' -Wait -PassThru; $p.ExitCode

prints 0 when the report is written, 1 when the file cannot be assessed, 2 for a wrong option. The program has
no console, so what it would print goes to station-report.pdf.log.txt next to the report: what was read from the
file, what it could not tell, and the reason for a refusal. Options: --station (one station of a file with a
Nätstation column), --smhi (the nearest SMHI station), --p0 / --pk / --uk (a unit's own losses), --unit, --tz,
--stamp, --decimal, --dayfirst / --monthfirst, --measured-at, --max-gap, --enclosure. The result is indicative:
generic transformer constants, not validated on a real station.

Check an installation
---------------------
The program can check itself without opening a window (station, PDF, fleet, scenario runs, cancel, close). In
PowerShell, in the HotspotLV folder:

    $p = Start-Process .\HotspotLV.exe -ArgumentList '--self-test', 'C:\Temp\hotspot-check' -Wait -PassThru; $p.ExitCode

takes about two minutes and must print 0. C:\Temp\hotspot-check\self-test.txt then shows every step; its last line
says ALL PASSED. For a folder with spaces, put it in double quotes inside the single ones:
'"C:\Temp\my check"'. (A plain .\HotspotLV.exe --self-test ... returns at once, because the program has no
console; the check still runs.)
