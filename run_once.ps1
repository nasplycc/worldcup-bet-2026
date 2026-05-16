$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
python .\main.py --mode upcoming --days 45 --plays all
