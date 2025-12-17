#!/bin/bash

python -m venv venv
source venv/bin/activate || source venv/Scripts/activate

pip install --upgrade pip
pip install -r requirements.txt

playwright install

uvicorn app.main:app --host 0.0.0.0 --port 8000
