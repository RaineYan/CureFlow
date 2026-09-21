@echo off
REM ============================================================
REM Day 14A R3 + 5-trial user-side runner.
REM
REM Safety contract:
REM   1. The API key is set by YOU in PowerShell via:
REM        $env:REAL_LLM_API_KEY = "<your Qwen key>"
REM   2. This .bat file NEVER echoes the key value.
REM   3. day14_pilot_run.py reads the env var in-process
REM      and never logs it.
REM   4. After this .bat finishes, manually unset the env
REM      var in your PowerShell session:
REM        Remove-Item Env:REAL_LLM_API_KEY
REM
REM The 6 trials are: R3 (case_a FULL) + 5 remaining.
REM Each trial writes a *_live_ledger.jsonl + (on failure)
REM a *_failure_audit.json next to its trial JSON.
REM ============================================================

setlocal EnableDelayedExpansion

echo ============================================================
echo  Day 14A R3 + 5-trial runner
echo ============================================================
echo.

REM Verify key presence WITHOUT printing the value
if "%REAL_LLM_API_KEY%"=="" (
    echo ERROR: REAL_LLM_API_KEY is NOT set.
    echo.
    echo Run this FIRST in your PowerShell session:
    echo   $env:REAL_LLM_API_KEY = "your-key"
    echo.
    echo Then re-run this .bat.
    exit /b 1
)

echo [OK] REAL_LLM_API_KEY is set (value NOT echoed)
echo [OK] provider=qwen model=qwen3.7-plus-2026-05-26
echo.

cd /d "%~dp0"

REM -------- R3: case_a FULL_RERUN (replacement of R2) --------
echo.
echo === [1/6] R3: case_a FULL_RERUN (replacement of trial01R2) ===
python day14_pilot_run.py ^
    --case case_a ^
    --strategy FULL_RERUN ^
    --replacement-of case_a_full_rerun_trial01R2
set R3_RC=%ERRORLEVEL%
echo   R3 exit code: %R3_RC%

REM -------- Trial 2: case_a COARSE_CHECKPOINT --------
echo.
echo === [2/6] case_a COARSE_CHECKPOINT ===
python day14_pilot_run.py --case case_a --strategy COARSE_CHECKPOINT
set T2_RC=%ERRORLEVEL%
echo   T2 exit code: %T2_RC%

REM -------- Trial 3: case_a SELECTIVE_RECOVERY --------
echo.
echo === [3/6] case_a SELECTIVE_RECOVERY ===
python day14_pilot_run.py --case case_a --strategy SELECTIVE_RECOVERY
set T3_RC=%ERRORLEVEL%
echo   T3 exit code: %T3_RC%

REM -------- Trial 4: case_b FULL_RERUN --------
echo.
echo === [4/6] case_b FULL_RERUN ===
python day14_pilot_run.py --case case_b --strategy FULL_RERUN
set T4_RC=%ERRORLEVEL%
echo   T4 exit code: %T4_RC%

REM -------- Trial 5: case_b COARSE_CHECKPOINT --------
echo.
echo === [5/6] case_b COARSE_CHECKPOINT ===
python day14_pilot_run.py --case case_b --strategy COARSE_CHECKPOINT
set T5_RC=%ERRORLEVEL%
echo   T5 exit code: %T5_RC%

REM -------- Trial 6: case_b SELECTIVE_RECOVERY --------
echo.
echo === [6/6] case_b SELECTIVE_RECOVERY ===
python day14_pilot_run.py --case case_b --strategy SELECTIVE_RECOVERY
set T6_RC=%ERRORLEVEL%
echo   T6 exit code: %T6_RC%

echo.
echo ============================================================
echo  ALL 6 TRIALS COMPLETE
echo ============================================================
echo R3 (case_a FULL):              %R3_RC%
echo case_a COARSE:                 %T2_RC%
echo case_a SELECTIVE:              %T3_RC%
echo case_b FULL:                   %T4_RC%
echo case_b COARSE:                 %T5_RC%
echo case_b SELECTIVE:              %T6_RC%
echo.
echo Next steps:
echo   1. Remove-Item Env:REAL_LLM_API_KEY
echo   2. Send the file list of experiments\results\day14\pilot\*.json to the agent
echo.

endlocal
