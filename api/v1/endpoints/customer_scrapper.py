from typing import List, Optional
from fastapi import APIRouter, HTTPException, Depends, Query

from core import settings
from schemas.config_handler import CustomerData as ConfigCustomerData
from schemas.customers_scrapper import (
    DataPSB, CustomerwithInvoices, Customer, CustomerBillingInfo,
    CustomerData, CustomerSearchResponse, CustomerDataWithInvoices, CustomerInvoice
)
from services.biling_scaper import BillingScraper, NOCScrapper
from services.supabase_client import search_customers
from services.playwright import CustomerService, NOC

router = APIRouter()

def get_scraper() -> NOCScrapper:
    try:
        return NOCScrapper()
    except ConnectionError as e:
        raise HTTPException(status_code=503, detail=f"NMS unavailable: {e}")

def get_billing() -> BillingScraper:
    """Create BillingScraper with its own session - billing requires separate auth from NMS."""
    try:
        return BillingScraper()  # Let it create its own session and login to billing
    except ConnectionError as e:
        raise HTTPException(status_code=503, detail=f"Billing unavailable: {e}")

def get_customer_service() -> CustomerService:
    try:
        return CustomerService()
    except ConnectionError as e:
        raise HTTPException(status_code=503, detail=f"Playwright unavailable: {e}")

def get_noc() -> NOC:
    try:
        return NOC()
    except ConnectionError as e:
        raise HTTPException(status_code=503, detail=f"Playwright unavailable: {e}")

# Endpoint show psb avaible
@router.get("/psb", response_model=List[DataPSB])
def get_psb_data(scraper: NOCScrapper = Depends(get_scraper)):
    return scraper._get_data_psb()

@router.get("/customers-billing", response_model=List[Customer])
def get_customer_details_route(
    query: str = Query(..., min_length=1),
    billing_scraper: BillingScraper = Depends(get_billing),
):
    # 1. Search for customers to get their IDs
    search_results = billing_scraper.search(query)
    
    if not search_results:
        raise HTTPException(status_code=404, detail=f"No customer found for query: '{query}'")

    detailed_customers = []

    # 2. Iterate through search results and fetch full details for each
    for result in search_results:
        cid = result.get("id")
        if cid:
            customer_obj = billing_scraper.get_customer_details(cid)
            
            if customer_obj:
                detailed_customers.append(customer_obj)

    if not detailed_customers:
        raise HTTPException(status_code=404, detail="Found customers but failed to retrieve details.")

    return detailed_customers


@router.get("/customers-data", response_model=List[CustomerData])
async def get_customer_data(
    search: str = Query(..., min_length=1, description="Search by name, address, or pppoe")
):
    """Get customer data from Supabase."""
    def _clean_field(value: any) -> Optional[str]:
        if not value: return None
        clean_value = str(value).strip()
        if clean_value in ("", "0", "-", "N/A"):  
            return None
        return clean_value
    try:
        customers = search_customers(search)
        if not customers:
            raise HTTPException(status_code=404, detail=f"No customer found for query: '{search}'")
        result = []
        
        for c in customers:
            result.append(CustomerData(
                name=c.get("nama", "Unknown"),
                address=_clean_field(c.get("alamat", "")),
                pppoe_user=c.get("user_pppoe", ""),
                pppoe_password=_clean_field(c.get("pppoe_password", "")),
                olt_name=_clean_field(c.get("olt_name", "")),
                interface=_clean_field(c.get("interface", "")),
                onu_sn=_clean_field(c.get("onu_sn", "")),
                modem_type=_clean_field(c.get("modem_type", "")),
            ))
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch customers: {e}")
    
@router.get("/customer-fast", response_model=CustomerSearchResponse)
async def get_customer_data_fast(
    search: str = Query(..., min_length=1, description="Search by name, address, or pppoe")
):
    """
    Get customer data using playwright.
    
    Returns:
        - If multiple customers found: List of basic customer info for selection
        - If single customer found: Full customer details with invoices
    """
    from services.playwright import get_customer_with_invoices_sync, run_sync
    
    # Run sync playwright in thread pool (Windows compatible)
    search_results, invoices_data = await run_sync(
        get_customer_with_invoices_sync, search, True
    )
    
    if not search_results:
        raise HTTPException(status_code=404, detail=f"No customer found for query: '{search}'")

    # Multiple results: return list for frontend selection
    if len(search_results) > 1:
        customers = [CustomerData(**c) for c in search_results]
        return CustomerSearchResponse(
            multiple=True,
            count=len(customers),
            customers=customers,
            customer=None
        )
    
    # Single result: with invoices already fetched
    customer_dict = search_results[0]
    
    invoices = None
    if invoices_data:
        invoices = CustomerInvoice(**invoices_data)
    
    customer_with_invoices = CustomerDataWithInvoices(
        **customer_dict,
        invoices=invoices
    )
    
    return CustomerSearchResponse(
        multiple=False,
        count=1,
        customers=None,
        customer=customer_with_invoices
    )
