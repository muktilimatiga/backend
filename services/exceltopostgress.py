import os
import re
import datetime as dt
import pandas as pd
import psycopg2
from psycopg2.extras import execute_batch

from services.supabase_client import supabase

# --- Configuration ---
POSTGRES_URI = os.getenv(
    "POSTGRES_URI",
    "dbname=data user=root password=Noclex1965 host=localhost port=5435"
)
TABLE_NAME = os.getenv("POSTGRES_TABLE", "data_fiber")
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "500"))  # Supabase batch limit


class ExcelHandler:
    """
    Encapsulates the logic for parsing the Excel file and syncing to DB.
    """
    
    CANDIDATE_COLS = {
        "name": ["nama","name","customer","pelanggan"],
        "pppoe": ["user pppoe","user_pppoe","pppoe","no internet","no. internet","internet","id internet"],
        "address": ["alamat","address","addr"],
        "onu_port": ["port onu","onu port","port","port_onu"],
        "onu_sn": ["no. sn","sn","serial","no sn","onu sn","serial number","serial_number"],
        "password": ["password","pppoe password","pw","pass"],
        "paket": ["paket", "Paket", "PAKET"],
    }

    @staticmethod
    def parse_sheet_name(name: str):
        n = (name or "").strip()
        # Skip summary or total sheets
        if not n or n.upper().startswith("TOTAL") or n.upper() in {"FIBER","SUMMARY","SHEET1"}:
            return None, None
        
        # Regex to extract OLT and Port from sheet name (e.g., "OLT BEJI 1.1")
        m = re.search(r"^(?P<olt>[A-Z]+)(?:\s+[A-Z0-9\s]+?)?(?:\s+PORT)?\s+(?P<port>[\d\.]+)\s*$", n, re.I)
        if not m:
            return n.upper(), None
        return m.group("olt").strip().upper(), m.group("port").strip()

    @staticmethod
    def norm_cols(df: pd.DataFrame) -> pd.DataFrame:
        """Normalize column names to lowercase stripped strings."""
        return df.rename(columns=lambda c: str(c).strip().lower() if c else "")

    @staticmethod
    def pick(df: pd.DataFrame, keys: list[str]) -> str | None:
        """Find the first matching column name from a list of candidates."""
        for k in keys:
            if k in df.columns:
                return k
        return None

    @classmethod
    def docs_from_sheet(cls, xl: pd.ExcelFile, sheet: str):
        olt_name, olt_port = cls.parse_sheet_name(sheet)
        if not olt_name:
            return

        try:
            # 1. Detect Header Row (use dtype=str to preserve zeros)
            temp_df = xl.parse(sheet, header=None, dtype=str)
            header_row_index = -1

            for i, row in temp_df.head(20).iterrows():
                row_str = ' '.join(str(s).lower() for s in row.dropna())
                # Heuristic: Header row must contain 'nama' and either 'pppoe' or 'alamat'
                if "nama" in row_str and ("pppoe" in row_str or "alamat" in row_str):
                    header_row_index = i
                    break

            if header_row_index == -1:
                print(f"[WARN] Could not find valid header in '{sheet}'. Skipping.")
                return

            # 2. Parse Actual Data
            df = xl.parse(sheet, header=header_row_index, dtype=str).fillna("")

        except Exception as e:
            print(f"[WARN] Error reading '{sheet}': {e}")
            return

        df = cls.norm_cols(df)
        cols = {k: cls.pick(df, v) for k, v in cls.CANDIDATE_COLS.items()}

        # Validation: Must have Name + (PPPoE OR Address)
        required_ok = (cols["name"] and (cols["pppoe"] or cols["address"]))
        if not required_ok:
            return

        # Helper to ensure string values preserve zeros (Excel sometimes converts to float)
        def ensure_string(val):
            if val is None:
                return ""
            val_str = str(val).strip()
            # If Excel converted to float (e.g., "101006813.0"), remove decimal
            if val_str.endswith(".0"):
                val_str = val_str[:-2]
            return val_str

        # 3. Yield Rows
        for _, r in df.iterrows():
            name = ensure_string(r.get(cols["name"], "")) if cols["name"] else ""
            pppoe = ensure_string(r.get(cols["pppoe"], "")) if cols["pppoe"] else ""
            addr = ensure_string(r.get(cols["address"], "")) if cols["address"] else ""
            paket = ensure_string(r.get(cols["paket"], "")) if cols["paket"] else ""

            # Skip empty rows
            if not (name or pppoe or addr):
                continue

            onu_port_val = (r.get(cols["onu_port"], "").strip() if cols["onu_port"] else None) or None
            final_olt_port = olt_port
            onu_id_val = None

            # Handle interface splitting (e.g., "1/1/1:5")
            if onu_port_val and ":" in onu_port_val:
                parts = onu_port_val.split(':', 1)
                final_olt_port = parts[0]
                if len(parts) > 1:
                    onu_id_val = parts[1]

            yield {
                "user_pppoe": pppoe,
                "nama": name,
                "alamat": addr,
                "olt_name": olt_name,
                "olt_port": final_olt_port,
                "onu_sn": (r.get(cols["onu_sn"], "").strip().upper() if cols["onu_sn"] else None) or None,
                "pppoe_password": (r.get(cols["password"], "").strip() if cols["password"] else None) or None,
                "interface": onu_port_val,
                "onu_id": onu_id_val,
                "sheet": sheet,
                "paket": paket,
                "updated_at": dt.datetime.utcnow().isoformat(),
            }

    # --- PostgreSQL Methods (Local Backup) ---
    
    @staticmethod
    def init_postgres(cur):
        """Create table in local PostgreSQL if it doesn't exist."""
        cur.execute(f"""
        CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
            user_pppoe TEXT PRIMARY KEY,
            nama TEXT,
            alamat TEXT,
            olt_name TEXT,
            olt_port TEXT,
            onu_sn TEXT,
            pppoe_password TEXT,
            interface TEXT,
            onu_id TEXT,
            sheet TEXT,
            paket TEXT,
            updated_at TIMESTAMP
        );
        """)

    @staticmethod
    def upsert_postgres(cur, rows: list[dict]):
        """Batch upsert into local PostgreSQL."""
        if not rows:
            return
        
        # Convert dicts to tuples for execute_batch
        row_tuples = [
            (
                r["user_pppoe"], r["nama"], r["alamat"], r["olt_name"],
                r["olt_port"], r["onu_sn"], r["pppoe_password"], r["interface"],
                r["onu_id"], r["sheet"], r["paket"], r["updated_at"]
            )
            for r in rows
        ]
        
        sql = f"""
        INSERT INTO {TABLE_NAME} (
            user_pppoe, nama, alamat, olt_name, olt_port, onu_sn,
            pppoe_password, interface, onu_id, sheet, paket, updated_at
        )
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (user_pppoe)
        DO UPDATE SET
            nama = EXCLUDED.nama,
            alamat = EXCLUDED.alamat,
            olt_name = EXCLUDED.olt_name,
            olt_port = EXCLUDED.olt_port,
            onu_sn = EXCLUDED.onu_sn,
            pppoe_password = EXCLUDED.pppoe_password,
            interface = EXCLUDED.interface,
            onu_id = EXCLUDED.onu_id,
            sheet = EXCLUDED.sheet,
            paket = EXCLUDED.paket,
            updated_at = EXCLUDED.updated_at;
        """
        execute_batch(cur, sql, row_tuples, page_size=1000)

    # --- Supabase Methods (Primary) ---
    
    @staticmethod
    def upsert_supabase(rows: list[dict]) -> int:
        """Batch upsert into Supabase."""
        if not rows:
            return 0
        
        response = supabase.table(TABLE_NAME).upsert(
            rows,
            on_conflict="user_pppoe"
        ).execute()
        
        return len(response.data) if response.data else 0

    # --- Main Process ---
    
    @classmethod
    def process_file(cls, file_obj) -> int:
        """Main entry point: reads file object, writes to BOTH Supabase and local PostgreSQL."""
        conn = None
        cur = None
        
        try:
            # Connect to local PostgreSQL (backup)
            try:
                conn = psycopg2.connect(POSTGRES_URI)
                cur = conn.cursor()
                cls.init_postgres(cur)
                conn.commit()
                print("[INFO] Connected to local PostgreSQL for backup")
            except Exception as pg_err:
                print(f"[WARN] Local PostgreSQL not available: {pg_err}")
                conn = None
                cur = None
            
            # Parse Excel
            xl = pd.ExcelFile(file_obj)
            sheet_count = len(xl.sheet_names)
            print(f"Processing {sheet_count} sheets...")

            rows_buffer = []
            total_upserted = 0

            for sheet in xl.sheet_names:
                for doc in cls.docs_from_sheet(xl, sheet) or []:
                    # Skip rows without user_pppoe (required for upsert)
                    if not doc.get("user_pppoe"):
                        continue
                    
                    rows_buffer.append(doc)

                    if len(rows_buffer) >= BATCH_SIZE:
                        # Upsert to Supabase (primary)
                        total_upserted += cls.upsert_supabase(rows_buffer)
                        
                        # Upsert to local PostgreSQL (backup)
                        if cur:
                            cls.upsert_postgres(cur, rows_buffer)
                            conn.commit()
                        
                        rows_buffer.clear()
                        print(f"Upserted {total_upserted} rows so far...")

            # Flush remaining rows
            if rows_buffer:
                total_upserted += cls.upsert_supabase(rows_buffer)
                if cur:
                    cls.upsert_postgres(cur, rows_buffer)
                    conn.commit()

            print(f"Total upserted: {total_upserted} rows (Supabase + PostgreSQL backup)")
            return total_upserted

        except Exception as e:
            print(f"[ERROR] Failed to process file: {e}")
            raise e
        
        finally:
            # Clean up PostgreSQL connection
            if cur:
                cur.close()
            if conn:
                conn.close()
