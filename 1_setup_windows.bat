powershell.exe -noprofile -executionpolicy bypass -file .\download.ps1
Miniconda3-4.4.10-Windows-x86_64.exe /S /AddToPath=0 /InstallationType=JustMe /RegisterPython=0 /NoRegistry=0
%USERPROFILE%\Miniconda3\Scripts\conda.exe create --name label --file requirement.txt -y