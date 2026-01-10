import sys
from pathlib import Path

# Add parent directory to sys.path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from supabase import create_client
from core import settings

# Initialize Supabase client
supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)

def search_customers(search_term: str, limit: int = 20):
    """Search customers by name, alamat, or user_pppoe.
    
    Splits the search term by spaces and matches ALL words (AND logic).
    E.g., "nasrul beji" will match records containing BOTH "nasrul" AND "beji".
    Each word can appear in any field (nama, alamat, or user_pppoe).
    """
    # Split search term into individual words and convert to uppercase
    words = search_term.strip().upper().split()
    
    # Start building the query
    query = supabase.table("customers_view").select("*")
    
    # For each word, add an OR condition across all searchable fields
    # Multiple .or_() calls are chained as AND
    for word in words:
        query = query.or_(f"nama.ilike.%{word}%,alamat.ilike.%{word}%,user_pppoe.ilike.%{word}%")
    
    response = query.limit(limit).execute()
    return response.data

def search_mitra(search_term: str, limit: int = 10):
    """Search Mitra """
    words = search_term.strip().upper().split()
    query = supabase.table().select("*")
    for word in words:
        query = query.or_()
    
    response = query.limit(limit).execute()
    return response.data

def search_monitoring(search_term: str, limit: int = 20):
    """Search Monitoring"""
    words = search_term.strip().upper().split()
    query = supabase.table("monitoring").select("*")
    for word in words:
        query = query.or_(f"nama.ilike.%{word}%,alamat.ilike.%{word}%,user_pppoe.ilike.%{word}%")
    response = query.limit(limit).execute()
    return response.data


async def save_customer_config(
    user_pppoe: str,
    nama: str,
    alamat: str,
    olt_name: str,
    interface: str,
    onu_sn: str,
    pppoe_password: str = None,
    paket: str = None,
) -> bool:
    """
    Save or update customer configuration to Supabase data_fiber table.
    Uses upsert on user_pppoe as the unique key.
    Returns True if successful, False otherwise.
    """
    import asyncio
    import datetime as dt
    import logging
    
    try:
        # Parse interface to get olt_port and onu_id
        olt_port = None
        onu_id = None
        if interface and ":" in interface:
            parts = interface.split(":", 1)
            port_str = parts[0]
            if "_" in port_str:
                olt_port = port_str.split("_")[-1]
            elif "-" in port_str:
                olt_port = port_str.split("-")[-1]
            else:
                olt_port = port_str
            onu_id = parts[1] if len(parts) > 1 else None
        
        data = {
            "user_pppoe": user_pppoe,
            "nama": nama,
            "alamat": alamat,
            "olt_name": olt_name,
            "olt_port": olt_port,
            "onu_sn": onu_sn,
            "pppoe_password": pppoe_password,
            "interface": interface,
            "onu_id": onu_id,
            "paket": paket,
            "updated_at": dt.datetime.utcnow().isoformat(),
        }
        
        def _upsert():
            return supabase.table("data_fiber").upsert(
                data, 
                on_conflict="user_pppoe"
            ).execute()
        
        response = await asyncio.to_thread(_upsert)
        logging.info(f"[SUPABASE] Saved customer config: {user_pppoe}")
        return True
        
    except Exception as e:
        logging.error(f"[SUPABASE] Failed to save customer config: {e}")
        return False
