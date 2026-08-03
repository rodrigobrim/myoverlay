@echo off
rem `mt` as a command, the way a dev checkout gets it from `uv run mt`.
rem
rem It lives in the install directory (the MSI puts that directory on the
rem machine PATH), and resolves the exe through %~dp0 - the folder THIS file
rem sits in - so it always drives the launcher it was installed beside, never
rem whichever myoverlay.exe happens to come first on PATH.
"%~dp0myoverlay.exe" %*
