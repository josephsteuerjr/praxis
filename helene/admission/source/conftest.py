"""Edition test identity: never exercise a configured real Telegram root room."""
import os

# This file is loaded by pytest only; production cannot inherit the test value.
os.environ['PRAXIS_ABSTRACTDL_ROOT_CHAT_ID'] = '-100999999999999999'
