import requests
import pandas as pd
from pathlib import Path
import random

class SQLClient :
    def __init__(self):
        pass
    def fetch_building_data(self , building_id):
        # return requests.get(f"http://your-sql-api/buildings/{building_id}").json()
        res = requests.get(f"https://oden.abe.kth.se/api/v1/buildings/492db706-b583-4211-8f51-34dc7a3f6d7d/").json()
        return res
        
    def building_by_address(self, extracted) : 
        req = f"http://localhost:8001/buildings/single_filter?filter_name=epc_idadr&filter_value={extracted}"
        response = requests.get(req)    
        return response   
        
    def get_address_by_username(self , thread_id) : 
        user_name = thread_id.split(':')[0]
        return random.choice(['Teknigringen 10B', 'Malvinas Väg 10', 'Drottning Kristinas väg 43B', 'Professorsslingan 51'])
        
class MockSQLClient:
    """
    File-backed mock of your SQLClient API using the augmented CSV.
    Columns auto-detected: building_id, address, username, thread_id.
    """
    def __init__(self, csv_path: str = "./src/data/buildings_augmented.csv"):
        self._path = Path(csv_path)
        self._df = pd.read_csv(self._path)
        cols = {c.lower(): c for c in self._df.columns}

        # Best-effort mapping
        self._building_id_col = cols.get("building_id") or next(iter(self._df.columns))
        self._address_col = cols.get("address")
        if not self._address_col:
            # fallbacks common in EPC exports
            for k in ["epc_idadr", "idadr", "raw_address", "addr"]:
                if k in cols:
                    self._address_col = cols[k]
                    break
        if not self._address_col:
            # as last resort, take a non-id column
            non_id_cols = [c for c in self._df.columns if c != self._building_id_col]
            self._address_col = non_id_cols[0] if non_id_cols else self._building_id_col

        self._username_col = cols.get("username") or "username"
        self._thread_id_col = cols.get("thread_id") or "thread_id"

    # --- Your three functions, mocked ---

    def fetch_building_data(self, building_id):
        rows = self._df[self._df[self._building_id_col] == building_id]
        return rows.iloc[0].to_dict() if not rows.empty else None

    def building_by_address(self, extracted):
        # naive contains match (case-insensitive)
        s = self._df[self._address_col].astype(str).str.lower()
        mask = s.str.contains(str(extracted).lower())
        rows = self._df[mask]
        # mimic your API: list of matches with building_id/address
        return [
            {"building_id": r[self._building_id_col], "address": r[self._address_col]}
            for _, r in rows.iterrows()
        ]

    def get_address_by_username(self, thread_id):
        username = str(thread_id).split(":")[0]
        rows = self._df[self._df[self._username_col] == username]
        if rows.empty:
            # fallback: random known address
            return random.choice(self._df[self._address_col].dropna().astype(str).tolist())
        return rows.iloc[0][self._address_col]