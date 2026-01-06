from . import BaseModel, Optional, List, HttpUrl

class Customer(BaseModel):
    id: str
    name: Optional[str] = None
    address: Optional[str] = None
    user_pppoe: Optional[str] = None
    coordinate: Optional[str] = None
    user_join: Optional[str] = None
    mobile: Optional[str] = None
    detail_url: Optional[HttpUrl] = None

class DataPSB(BaseModel):
    name: Optional[str] = None
    address: Optional[str] = None
    user_pppoe: Optional[str] = None
    pppoe_password: Optional[str] = None
    paket: Optional[str] = None


class InvoiceItem(BaseModel):
    status: Optional[str] = None
    package: Optional[str] = None
    period: Optional[str] = None
    month: Optional[int] = None
    year: Optional[int] = None
    payment_link: Optional[str] = None
    description: Optional[str] = None

class BillingSummary(BaseModel):
    this_month: Optional[str] = None
    arrears_count: int = 0
    last_paid_month: Optional[str] = None

class CustomerwithInvoices(Customer):
    paket: Optional[str] = None
    invoices: Optional[List[InvoiceItem]] = None
    summary: Optional[BillingSummary] = None

    class Config:
        from_attributes = True