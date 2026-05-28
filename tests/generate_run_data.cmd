@echo off
rem generate_run_data.cmd
rem
rem Generate debug run-data JSONs for resume samples by calling the service API.
rem
rem Usage:
rem   generate_run_data.cmd                          -- regenerate all samples (1-40)
rem   generate_run_data.cmd 4                        -- sample 4 only
rem   generate_run_data.cmd 4 16 18                  -- samples 4, 16, 18
rem   generate_run_data.cmd --classification-only    -- classify all, skip generation
rem   generate_run_data.cmd --classification-only 4 16 18
rem
rem Optional env vars (all have defaults):
rem   SERVICE_URL        -- backend base URL  (default: http://localhost:8000)
rem   USER_LOGIN         -- regular user email
rem   USER_PASSWORD      -- regular user password
rem   ADMIN_LOGIN        -- admin email
rem   ADMIN_PASSWORD     -- admin password
rem   JOB_DESCRIPTION_ID -- target job description ID (default: 97)

setlocal
set TESTS_DIR=%~dp0
python "%TESTS_DIR%generate_run_data.py" %*
endlocal
