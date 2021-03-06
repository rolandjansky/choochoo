#!/bin/bash

pushd py
rm -fr env
python3.8 -m venv env
source env/bin/activate

pip install --upgrade pip
pip install setuptools wheel twine

pip install bokeh
pip install cachetools
pip install colorama
pip install colorlog
pip install jupyter
pip install matplotlib
pip install numpy
pip install openpyxl
pip install pandas==0.25.3
pip install pendulum
pip install psutil
pip install psycopg2
pip install pyGeoTile
pip install pyproj
pip install rasterio
pip install requests
pip install scipy
pip install sklearn
pip install sqlalchemy
pip install sqlalchemy-utils
pip install sqlalchemy-batch-inserts
pip install uritools
pip install werkzeug

pip install s-tui

echo
echo "creating link to so that ch2 package appears in the environment"
ln -s `pwd`/ch2 env/lib/python3.7/site-packages/ch2
echo "to remove:"
echo "  rm py/env/lib/python3.7/site-packages/ch2"
echo "but jupyter will then fail to find ch2 unless it is installed or you"
echo "modify .ipython/profile_default/ipython_config.py"
echo
echo "source py/env/bin/activate"
echo
