#schemas/config.py

from pydantic import BaseModel
from typing import List, Optional, Dict, Any

class UnconfiguredOnt(BaseModel):
    sn: str
    pon_port: str
    pon_slot: str

class CustomerInfo(BaseModel):
    name: str
    address: str
    pppoe_user: str
    pppoe_pass: str

class CustomerData(BaseModel):
    name: str
    address: str
    pppoe_user: str
    pppoe_password: str
    olt_name: str
    interface: str
    onu_sn: str
    modem_type: str
    
class ConfigurationRequest(BaseModel):
    sn: str
    customer: CustomerInfo
    package: str
    modem_type: str
    eth_locks: List[bool]

class ConfigurationSummary(BaseModel):
    status: str
    message: str
    serial_number: str
    name: str
    pppoe_user: str
    location: str
    profile: str
    report: str

class ConfigurationResponse(BaseModel):
    message: str
    summary: Optional[ConfigurationSummary] = None
    logs: List[str]

class OptionsResponse(BaseModel):
    olt_options: List[str]
    modem_options: List[str]
    package_options: List[str]

class ConfigurationBridgeRequest(BaseModel):
    sn: str
    customer: CustomerInfo
    modem_type: str
    package: str
    vlan: str


class CongigurationBridgeResponse(BaseModel):
    olt_name: str
    modem_options: str
    package_options: str

# --- SCHEMA BARU UNTUK ONU DETAIL ---

class OnuLogEntry(BaseModel):
    """Mewakili satu baris log Authpass/Offline time dari ONU."""
    id: int
    auth_time: str
    offline_time: str
    cause: str

class OnuDetail(BaseModel):
    """
    Menggabungkan field yang diekstrak dari 'sh gpon onu detail-info'
    dan dua log modem terakhir.
    """
    # Field utama dari parsing
    onu_interface: Optional[str] = None
    type: Optional[str] = None
    phase_state: Optional[str] = None
    serial_number: Optional[str] = None
    onu_distance: Optional[str] = None
    online_duration: Optional[str] = None
    
    # Log modem terakhir
    modem_logs: List[OnuLogEntry] = []

class BatchConfigurationRequest(BaseModel):
    items: List[ConfigurationRequest]

# Output: Status for a single item in the batch
class BatchItemResult(BaseModel):
    # It helps if your ConfigurationRequest has an ID or unique field (like sn or username)
    # to identify which result belongs to which request.
    identifier: str 
    success: bool
    message: str
    logs: List[str]

# Output: The final response for the whole batch
class BatchConfigurationResponse(BaseModel):
    total: int
    success_count: int
    fail_count: int
    results: List[BatchItemResult]
