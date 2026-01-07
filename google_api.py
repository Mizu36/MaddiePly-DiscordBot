import asyncio
import gspread
import random
from oauth2client.service_account import ServiceAccountCredentials
from local_database import get_setting
from tools import debug_print, set_reference, path_from_app_root

GOOGLE_CLIENT = None

class GoogleSheets:
    def __init__(self):
        set_reference("GoogleSheets", self)
        self.start_google_sheets()

    def start_google_sheets(self):
        global GOOGLE_CLIENT
        google_scope = ["https://spreadsheets.google.com/feeds",
                "https://www.googleapis.com/auth/drive"]
        cred_path = path_from_app_root("credentials.json")
        google_creds = ServiceAccountCredentials.from_json_keyfile_name(str(cred_path), google_scope)
        GOOGLE_CLIENT = gspread.authorize(google_creds)
        
    def open_sheet(self, sheet_id):
        global GOOGLE_CLIENT
        if GOOGLE_CLIENT is None:
            try:
                self.start_google_sheets()
            except Exception as e:
                print(f"Error initializing Google Sheets client: {e}")
                raise
        sheet = GOOGLE_CLIENT.open_by_key(sheet_id).sheet1
        return sheet

    async def get_quote(self, quote_id: int):
        debug_print("GoogleAPI", f"Getting quote {quote_id} from google sheet.")
        quotes_sheet_id = await get_setting("Google Sheets Quotes Sheet ID")
        if not quotes_sheet_id:
            raise ValueError("Google Sheets Quotes Sheet ID is not set in the settings.")
        sheet = self.open_sheet(quotes_sheet_id)
        quotes = sheet.get_all_records()
        for q in quotes:
            if q["ID"] == quote_id:
                return q
        return None

    async def get_random_quote(self):
        debug_print("GoogleAPI", "Getting a random quote from google sheet.")
        quotes_sheet_id = await get_setting("Google Sheets Quotes Sheet ID")
        if not quotes_sheet_id:
            raise ValueError("Google Sheets Quotes Sheet ID is not set in the settings.")
        sheet = self.open_sheet(quotes_sheet_id)
        quotes = sheet.get_all_records()
        if not quotes:
            return None
        random_quote = random.choice(quotes)
        return random_quote

    async def get_random_quote_containing_words(self, words: str):
        debug_print("GoogleAPI", f"Searching for a quote with the words '{words}' in the google sheet.")
        quotes_sheet_id = await get_setting("Google Sheets Quotes Sheet ID")
        if not quotes_sheet_id:
            raise ValueError("Google Sheets Quotes Sheet ID is not set in the settings.")
        sheet = self.open_sheet(quotes_sheet_id)
        quotes = sheet.get_all_records()
        filtered_quotes = [q for q in quotes if words.lower() in q["Quote"].lower()]
        if not filtered_quotes:
            return None
        random_quote = random.choice(filtered_quotes)
        return random_quote

if __name__ == "__main__":
    async def _demo():
        gs = GoogleSheets()
        quote = await gs.get_random_quote()
        print("Random Quote:", quote)

    asyncio.run(_demo())
