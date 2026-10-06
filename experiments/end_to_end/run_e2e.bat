@echo off
REM E2E processing-time benchmark (cold start). See experiments\end_to_end\README.md.
REM   run_e2e.bat --dry-run      prechecks + prepare only
REM   run_e2e.bat                full run, about 6 h for the five matches
cd /d "%~dp0..\.."
uv run python experiments\end_to_end\run_e2e.py %*
echo.
echo Finished. Results: experiments\end_to_end\results\summary.md
pause
