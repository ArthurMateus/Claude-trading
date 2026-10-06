# Start the paper-trading loop on Windows with logs in data\tradebot.log.
Set-Location (Join-Path $PSScriptRoot "..")
& .\.venv\Scripts\tradebot.exe --log-file data\tradebot.log run
