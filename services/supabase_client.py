import sys
from pathlib import Path

# Add parent directory to sys.path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from supabase import create_client
from core.config import settings

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

