import os

os.system('uvicorn main:app --reload --reload-exclude "cache/*" --reload-exclude "*.log" --reload-exclude ".env"')
