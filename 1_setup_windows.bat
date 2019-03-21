powershell.exe -noprofile -executionpolicy bypass -file .\download.ps1
echo "We are installing miniconda to your machine. Please wait around 5 mins ..."
Miniconda3-4.4.10-Windows-x86_64.exe /S /AddToPath=0 /InstallationType=JustMe /RegisterPython=0 /NoRegistry=0
"%USERPROFILE%\Miniconda3\Scripts\conda.exe" create --name label --file requirement.txt -y
