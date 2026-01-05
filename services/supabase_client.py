import sys
from pathlib import Path

# Add parent directory to sys.path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from supabase import create_client
from core.config import settings

# Initialize Supabase client
supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)


def get_customers_view(limit: int = 100, offset: int = 0):
    """Fetch data from customers_view."""
    response = supabase.table("customers_view").select("*").limit(limit).offset(offset).execute()
    return response.data


def get_customer_by_pppoe(user_pppoe: str):
    """Fetch a single customer by user_pppoe."""
    response = supabase.table("customers_view").select("*").eq("user_pppoe", user_pppoe).single().execute()
    return response.data


def search_customers(search_term: str, limit: int = 20):
    """Search customers by name, alamat, or user_pppoe."""
    response = (
        supabase.table("customers_view")
        .select("*")
        .or_(f"nama.ilike.%{search_term}%,alamat.ilike.%{search_term}%,user_pppoe.ilike.%{search_term}%")
        .limit(limit)
        .execute()
    )
    return response.data


# Test block - run this file directly to test the connection
if __name__ == "__main__":
    print("Testing Supabase connection...")
    print("-" * 50)
    
    try:
        # Test fetching customers
        customers = get_customers_view(limit=5)
        print(f"Successfully fetched {len(customers)} customers!")
        print("-" * 50)
        
        for customer in customers:
            print(f"ID: {customer.get('customer_id')}")
            print(f"  Nama: {customer.get('nama')}")
            print(f"  PPPoE: {customer.get('user_pppoe')}")
            print(f"  OLT: {customer.get('olt_name')}")
            print(f"  Status: {customer.get('snmp_status')}")
            print()
        
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
