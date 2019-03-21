curl https://repo.continuum.io/miniconda/Miniconda3-4.4.10-MacOSX-x86_64.sh -o Miniconda3-4.4.10-MacOSX-x86_64.sh
chmod +x Miniconda3-4.4.10-MacOSX-x86_64.sh
echo "We are installing miniconda to your machine. Please wait around 5 mins ..."
./Miniconda3-4.4.10-MacOSX-x86_64.sh -b -p ~/miniconda3
~/miniconda3/bin/conda create --name label --file requirement.txt -y
